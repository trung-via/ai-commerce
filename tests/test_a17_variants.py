"""C07 actual A03/A04/A05/A06/A15 consumers, custody and adversarial replay."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import socket
import struct
import urllib.request
import zlib

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.composition import (
    Canvas, CompositionStore, MediaReference, compose_images, create_composition,
)
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import brief_digest, intake_brief
from ai_commerce.creative.reasoner import MockCreativeReasoner
from ai_commerce.creative_scaling import (
    CreativeScalingError, create_variant_batch, load_variant_batch, render_variant, summarize_variant_costs,
)
from ai_commerce.creative_scaling import schemas
from ai_commerce.creative_scaling.variants import batch_ref
from ai_commerce.e2e import inspect as inspect_e01, run as run_e01, verify as verify_e01
from ai_commerce.media import MediaRequest, MockMediaProvider
from ai_commerce.operations import ArtifactRegistry, LocalMediaRunner
from ai_commerce.operations.cost_ledger import CostEntry, CostEvidenceError, CostLedger
from ai_commerce.operations.generation import GenerationJobRunner
from ai_commerce.operations.status_report import media_job_status
from ai_commerce.providers.base import MockContentProvider
from ai_commerce.render import FFmpegRenderer
from ai_commerce.render.manifest import checksum
from ai_commerce.render.models import EncoderConfig
from ai_commerce.bridge import composition_to_render_plan


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A17 attempted network/provider/generation/publish")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(MockContentProvider, "generate", forbidden)
    monkeypatch.setattr(MockMediaProvider, "produce", forbidden)
    monkeypatch.setattr(MockCreativeReasoner, "generate", forbidden)
    for name in ("submit", "observe", "reconcile", "fetch"):
        monkeypatch.setattr(GenerationJobRunner, name, forbidden)


def png(red):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + bytes((red, 30, 170)) * 16) * 16)) + chunk(b"IEND", b""))


def file_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def scope_fixture():
    return {"platform": "TIKTOK", "surface": "VIDEO",
            "account_ref": {"namespace": "internal_alias", "id": "a11-account-" + schemas.digest(
                {"namespace": "internal_alias", "id": "fixture-account"})},
            "measurement_window": ["2026-10-01T00:00:00Z", "2026-10-03T00:00:00Z"],
            "timezone": "UTC", "cohort": "fixture-cohort", "market": "VN"}


def master_case(tmp_path, *, content_only=False):
    registry = ArtifactRegistry(tmp_path / "custody")
    brief = intake_brief({"brief_id": "fixture-master", "objective": "A fictional robot cat story",
                          "product_ref": None, "source_refs": [], "custom_constraint": "Keep the quiet tone"})
    if content_only:
        master = create_composition(brief["objective"], canvas=Canvas(32, 48, 12), source_brief=brief)
        edits = [{"mode": "FREEFORM", "intent": s, "asset_order": None, "frames": None, "cost_entry_ids": []}
                 for s in ("A quiet cat in a library", "A curious cat in a garden")]
    else:
        references = []
        for i, color in enumerate((35, 225)):
            path = tmp_path / f"still-{i}.png"
            path.write_bytes(png(color))
            manifest = LocalMediaRunner(registry).import_file(MediaRequest(
                mode="import", source_path=path, mime_type="image/png"), job_id=f"fixture-asset-{i}")
            references.append(MediaReference.from_manifest(manifest))
        master = compose_images(brief["objective"], [(r, 3) for r in references],
                                canvas=Canvas(32, 48, 12), source_brief=brief)
        a, b = [r.artifact_id for r in references]
        edits = [{"mode": "CONTROLLED", "intent": "The cat notices the garden first",
                  "asset_order": [b, a], "frames": [3, 3], "cost_entry_ids": []},
                 {"mode": "CONTROLLED", "intent": "A longer pause by the library",
                  "asset_order": [a, b], "frames": [2, 6], "cost_entry_ids": []}]
    CompositionStore(registry.root).save(master)
    return registry, master, edits


def create_case(tmp_path, **options):
    registry, master, edits = master_case(tmp_path, **options)
    batch = create_variant_batch(master, edits, registry=registry, batch_id="two-variants", scope=scope_fixture())
    return registry, master, edits, batch


def cost_entry(registry, index, *, status="ESTIMATED", amount="0", currency="USD", origin="USER_SUPPLIED"):
    manifest = LocalMediaRunner(registry).load(f"fixture-asset-{index % 2}")
    receipt_ref = f"fixture-costs/receipt-{index}.json" if status == "CHARGED" else None
    raw = canonical_bytes({"fixture_line": index, "amount": amount, "currency": currency})
    if receipt_ref:
        registry.put_immutable(receipt_ref, raw)
    entry = CostEntry(f"cost-{index}", manifest.job_id, 1, manifest.request_digest,
                      manifest.request["provider"]["name"], None, status, amount, currency,
                      receipt_ref, sha256_bytes(raw) if receipt_ref else None, origin, "2026-10-30T00:00:00Z")
    return CostLedger(registry).record(entry)


def test_two_concrete_variants_preserve_a03_a04_a05_sources_and_replay(tmp_path):
    registry, master, edits = master_case(tmp_path)
    original = file_bytes(registry.root)
    batch = create_variant_batch(master, edits, registry=registry, batch_id="batch-1")
    assert batch["product_ref"] is None and batch["scope"] is None
    assert batch["generation_performed"] is False and batch["publication_status"] == "NOT_ATTEMPTED"
    assert len({v["variant_id"] for v in batch["variants"]}) == 2
    assert len({v["composition"]["sha256"] for v in batch["variants"]}) == 2
    for variant in batch["variants"]:
        assert variant["parent"] == {"kind": "MASTER", "id": batch["master"]["master_id"], "revision": 1,
                                      "sha256": schemas.digest(batch["master"])}
        assert variant["brief"] == batch["master"]["brief"]
        assert variant["script"]["sha256"] != batch["master"]["script"]["sha256"]
        assert variant["composition"]["revision"] == 1  # Two forks, no competing r2 writes.
        assert variant["render"]["state"] == "NOT_ATTEMPTED" and variant["render"]["job_id"]
        assert not registry.exists(variant["render"]["manifest_ref"])
        assert all(a["provenance"] == "USER_SUPPLIED" and a["rights_status"] == "UNKNOWN"
                   and a["manifest_ref"] and a["manifest_sha256"] for a in variant["assets"])
    for ref, raw in original.items():
        assert registry.read(ref) == raw
    snapshot = file_bytes(registry.root)
    assert create_variant_batch(master, deepcopy(edits), registry=registry, batch_id="batch-1") == batch
    assert file_bytes(registry.root) == snapshot
    assert CompositionStore(registry.root).load(master.composition_id, 1).to_dict() == master.to_dict()


def test_identical_replay_is_order_independent_but_changed_digest_conflicts(tmp_path):
    registry, master, edits, batch = create_case(tmp_path)
    before = file_bytes(registry.root)
    reordered = [{key: edit[key] for key in reversed(list(edit))} for edit in edits]
    assert create_variant_batch(master, reordered, registry=registry, batch_id=batch["batch_id"],
                                scope=scope_fixture()) == batch
    edits[0]["frames"][0] += 1
    with pytest.raises(CreativeScalingError, match="BATCH_DIGEST_CONFLICT"):
        create_variant_batch(master, edits, registry=registry, batch_id=batch["batch_id"], scope=scope_fixture())
    assert file_bytes(registry.root) == before


@pytest.mark.parametrize("mutation", ["extra", "count", "mode", "blank", "bool-frame", "float-frame", "zero-frame",
                                     "length", "unknown-asset", "freeform-assets", "total", "duplicate", "provider"])
def test_invalid_edits_fail_before_writing(tmp_path, mutation):
    registry, master, edits = master_case(tmp_path)
    kwargs = {}
    if mutation == "extra": edits[0]["publish"] = True
    elif mutation == "count": edits = edits[:1]
    elif mutation == "mode": edits[0]["mode"] = "MODEL"
    elif mutation == "blank": edits[0]["intent"] = " "
    elif mutation == "bool-frame": edits[0]["frames"][0] = True
    elif mutation == "float-frame": edits[0]["frames"][0] = 3.0
    elif mutation == "zero-frame": edits[0]["frames"][0] = 0
    elif mutation == "length": edits[0]["frames"].pop()
    elif mutation == "unknown-asset": edits[0]["asset_order"][0] = "artifact:" + "f" * 64
    elif mutation == "freeform-assets": edits[0]["mode"] = "FREEFORM"
    elif mutation == "total": edits[0]["frames"][0] = 1441
    elif mutation == "duplicate": edits[1] = deepcopy(edits[0])
    elif mutation == "provider": kwargs["provenance"] = "PROVIDER_ATTESTED"
    before = file_bytes(registry.root)
    with pytest.raises(ValueError):
        create_variant_batch(master, edits, registry=registry, batch_id="invalid", **kwargs)
    assert file_bytes(registry.root) == before


@pytest.mark.parametrize("field,value", [("market", "vn"), ("surface", "PAGE_REEL"), ("timezone", "Bad/Zone"),
    ("measurement_window", ["2026-10-03T00:00:00Z", "2026-10-01T00:00:00Z"]),
    ("measurement_window", ["2026-10-01", "2026-10-03"]), ("cohort", "person@example.test")])
def test_bad_scope_rejected(tmp_path, field, value):
    registry, master, edits = master_case(tmp_path)
    scope = scope_fixture()
    scope[field] = value
    with pytest.raises(ValueError):
        create_variant_batch(master, edits, registry=registry, batch_id="scope", scope=scope)


@pytest.mark.parametrize("mutation", ["role", "script", "composition", "master", "parent", "manifest", "render", "product"])
def test_resealed_cross_source_and_role_forgery_is_rejected(tmp_path, mutation):
    registry, _, _, batch = create_case(tmp_path)
    forged = deepcopy(batch)
    a, b = forged["variants"]
    if mutation == "role": a["assets"][0]["role"] = "visual/wrong-scene"
    elif mutation == "script": a["script"] = b["script"]
    elif mutation == "composition": a["composition"] = b["composition"]
    elif mutation == "master": forged["master"]["composition"]["sha256"] = "f" * 64
    elif mutation == "parent": a["parent"]["sha256"] = b["composition"]["sha256"]
    elif mutation == "manifest": a["manifest_ref"] = b["manifest_ref"]
    elif mutation == "render": a["render"] = b["render"]
    elif mutation == "product": forged["product_ref"] = "unrelated-product"
    forged = schemas.seal({k: v for k, v in forged.items() if k != "record_sha256"})
    registry.safe_path(batch_ref(batch["batch_id"])).write_bytes(canonical_bytes(forged))
    with pytest.raises(ValueError):
        load_variant_batch(registry, batch["batch_id"])


@pytest.mark.parametrize("target", ["script", "variant-manifest", "source-bytes", "legacy-batch", "master-revision"])
def test_existing_corruption_is_never_healed_or_overwritten(tmp_path, target):
    registry, master, edits, batch = create_case(tmp_path)
    variant = batch["variants"][0]
    ref = {"script": variant["script"]["ref"], "variant-manifest": variant["manifest_ref"],
           "source-bytes": "objects/" + variant["assets"][0]["sha256"],
           "legacy-batch": batch_ref(batch["batch_id"]),
           "master-revision": batch["master"]["composition"]["ref"]}[target]
    registry.safe_path(ref).write_bytes(b'{"legacy":true}')
    before = file_bytes(registry.root)
    with pytest.raises(Exception):
        create_variant_batch(master, edits, registry=registry, batch_id=batch["batch_id"], scope=scope_fixture())
    assert file_bytes(registry.root) == before


def test_unsaved_master_and_unchanged_content_are_rejected(tmp_path):
    registry, master, edits = master_case(tmp_path, content_only=True)
    unsaved = create_composition("A different cat", canvas=master.canvas)
    with pytest.raises(Exception):
        create_variant_batch(unsaved, edits, registry=registry, batch_id="unsaved")
    edits[0]["intent"] = master.intent
    with pytest.raises(CreativeScalingError, match="UNCHANGED_VARIANT"):
        create_variant_batch(master, edits, registry=registry, batch_id="unchanged")


def test_freeform_content_only_has_no_sku_cta_storyboard_or_render_claim(tmp_path):
    registry, _, _, batch = create_case(tmp_path, content_only=True)
    for variant in batch["variants"]:
        assert variant["assets"] == [] and variant["render"] == {
            "state": "ASSET_REQUIRED", "job_id": None, "manifest_ref": None}
        brief = json.loads(registry.read(variant["brief"]["ref"]))
        assert brief["product_ref"] is None and "cta" not in brief and "storyboard" not in brief
        with pytest.raises(CreativeScalingError, match="ASSET_REQUIRED"):
            render_variant(batch, variant["variant_id"], registry=registry, human_authorized=True)


def test_a15_cost_entries_remain_separate_unknown_zero_and_supplied_charged(tmp_path):
    registry, master, edits = master_case(tmp_path)
    estimate = cost_entry(registry, 0, amount="0")
    charged = cost_entry(registry, 1, status="CHARGED", amount="1.2500")
    unknown = cost_entry(registry, 2, status="UNKNOWN", amount=None, currency=None)
    edits[0]["cost_entry_ids"] = [estimate.entry_id, charged.entry_id]
    edits[1]["cost_entry_ids"] = [unknown.entry_id]
    batch = create_variant_batch(master, edits, registry=registry, batch_id="costs")
    assert batch["variants"][0]["costs"] == [estimate.to_record(), charged.to_record()]
    assert batch["variants"][1]["costs"][0]["amount"] is None
    summary = summarize_variant_costs(batch, registry=registry, currency="USD")
    assert summary["totals"] == {"estimated": "0", "supplied_charged": "1.2500", "synthetic_charged": None}
    assert summary["cost_completeness"] == "UNKNOWN" and summary["provider_charge_verified"] is False
    status = media_job_status(LocalMediaRunner(registry).load(charged.job_id), CostLedger(registry), (charged.entry_id,))
    assert status["cost_observations"]["by_currency"]["USD"]["supplied_charged"] == "1.2500"
    assert status["publication_status"] == "NOT_ATTEMPTED" and status["additional_provider_calls"] is False


def test_cross_currency_and_cross_job_costs_do_not_become_batch_totals(tmp_path):
    registry, master, edits = master_case(tmp_path)
    usd, vnd = cost_entry(registry, 0, amount="1"), cost_entry(registry, 1, amount="100", currency="VND")
    edits[0]["cost_entry_ids"] = [usd.entry_id, vnd.entry_id]
    batch = create_variant_batch(master, edits, registry=registry, batch_id="currencies")
    with pytest.raises(CreativeScalingError, match="CROSS_CURRENCY"):
        summarize_variant_costs(batch, registry=registry, currency="USD")
    wrong = replace(usd, entry_id="wrong-source", intent_digest="f" * 64)
    CostLedger(registry).record(wrong)
    edits[0]["cost_entry_ids"] = [wrong.entry_id]
    with pytest.raises(CreativeScalingError, match="COST_SOURCE_MISMATCH"):
        create_variant_batch(master, edits, registry=registry, batch_id="wrong-source")


def test_duplicate_receipt_lines_across_variants_are_not_summed_twice(tmp_path):
    registry, master, edits = master_case(tmp_path)
    first = cost_entry(registry, 0, status="CHARGED", amount="1.25")
    duplicate = replace(first, entry_id="same-receipt-other-id")
    CostLedger(registry).record(duplicate)
    edits[0]["cost_entry_ids"] = [first.entry_id]
    edits[1]["cost_entry_ids"] = [duplicate.entry_id]
    batch = create_variant_batch(master, edits, registry=registry, batch_id="duplicate-receipts")
    with pytest.raises(CostEvidenceError, match="DUPLICATE_CHARGE_EVIDENCE"):
        summarize_variant_costs(batch, registry=registry, currency="USD")


def test_missing_costs_remain_null_and_synthetic_charges_remain_synthetic(tmp_path):
    registry, master, edits = master_case(tmp_path)
    batch = create_variant_batch(master, edits, registry=registry, batch_id="no-costs")
    summary = summarize_variant_costs(batch, registry=registry, currency="USD")
    assert summary["cost_completeness"] == "UNKNOWN" and set(summary["totals"].values()) == {None}
    charged = cost_entry(registry, 0, status="CHARGED", amount="0", origin="SYNTHETIC_FIXTURE")
    edits[0]["cost_entry_ids"] = [charged.entry_id]
    batch = create_variant_batch(master, edits, registry=registry, batch_id="synthetic-costs")
    summary = summarize_variant_costs(batch, registry=registry, currency="USD")
    assert summary["totals"] == {"estimated": None, "supplied_charged": None, "synthetic_charged": "0"}
    assert summary["provider_charge_verified"] is False


@pytest.mark.parametrize("mutation", ["bool-version", "float-version", "float-money", "unknown-zero", "missing-proof", "extra"])
def test_legacy_cost_records_cannot_be_normalized_into_valid_evidence(tmp_path, mutation):
    registry, master, edits = master_case(tmp_path)
    cost = cost_entry(registry, 0)
    record = cost.to_record()
    if mutation == "bool-version": record["version"] = True
    elif mutation == "float-version": record["version"] = 1.0
    elif mutation == "float-money": record["amount"] = 0.0
    elif mutation == "unknown-zero": record["status"] = "UNKNOWN"
    elif mutation == "missing-proof": record["status"] = "CHARGED"
    elif mutation == "extra": record["provider_attested"] = True
    record = schemas.seal({k: v for k, v in record.items() if k != "record_sha256"})
    registry.safe_path(CostLedger.ref(cost.entry_id)).write_bytes(canonical_bytes(record) + b"\n")
    edits[0]["cost_entry_ids"] = [cost.entry_id]
    before = file_bytes(registry.root)
    with pytest.raises(ValueError):
        create_variant_batch(master, edits, registry=registry, batch_id="bad-ledger")
    assert file_bytes(registry.root) == before


@pytest.mark.parametrize("amount", [0.1, float("nan"), "NaN", "Infinity", "-1", "1e3"])
def test_float_or_nonfinite_money_is_rejected_by_real_a15_consumer(tmp_path, amount):
    registry, _, _ = master_case(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        cost_entry(registry, 0, amount=amount)


def test_real_offline_variant_renders_have_manifest_and_full_decode_proof(tmp_path, monkeypatch):
    registry, master, _, batch = create_case(tmp_path)
    before_master = registry.read(batch["master"]["composition"]["ref"])
    before_batch = registry.read(batch_ref(batch["batch_id"]))
    proofs = []
    for variant in batch["variants"]:
        with pytest.raises(CreativeScalingError, match="AUTHORIZATION_REQUIRED"):
            render_variant(batch, variant["variant_id"], registry=registry)
        proof = render_variant(batch, variant["variant_id"], registry=registry, human_authorized=True)
        assert proof["state"] == "SUCCEEDED" and proof["full_decode_verified"] is True
        assert proof["manifest_ref"] == variant["render"]["manifest_ref"]
        assert proof["output"]["mime_type"] == "video/mp4" and proof["output"]["size_bytes"] > 0
        assert proof["output"]["verification"]["frame_count"] == sum(variant["edit"]["frames"])
        receipt = FFmpegRenderer(registry).load(proof["job_id"])
        assert receipt.to_dict()["record_sha256"] == proof["manifest_sha256"]
        assert receipt.plan.composition_sha256 == variant["composition"]["sha256"]
        assert proof["semantic_realization"] == "UNKNOWN" and proof["generation_performed"] is False
        proofs.append(proof)
    assert proofs[0]["output"]["sha256"] != proofs[1]["output"]["sha256"]
    monkeypatch.setattr(FFmpegRenderer, "render", lambda *a, **k: pytest.fail("replay rendered again"))
    assert render_variant(batch, batch["variants"][0]["variant_id"], registry=registry, human_authorized=True) == proofs[0]
    assert registry.read(batch["master"]["composition"]["ref"]) == before_master
    assert registry.read(batch_ref(batch["batch_id"])) == before_batch


def test_unknown_local_render_is_never_retried_or_claimed_usable(tmp_path, monkeypatch):
    registry, _, _, batch = create_case(tmp_path)
    variant = batch["variants"][0]
    pin = variant["composition"]
    comp = CompositionStore(registry.root).load(pin["id"], pin["revision"])
    plan = composition_to_render_plan(comp, composition_ref=pin["ref"])
    job_id = variant["render"]["job_id"]
    request = checksum({"request_version": 1, "job_id": job_id, "created_at": "2026-10-30T00:00:00Z",
                        "plan": plan.to_dict(), "encoder": EncoderConfig().to_dict()})
    registry.put_immutable(f"render-jobs/{job_id}/request.json", canonical_bytes(request))
    monkeypatch.setattr(FFmpegRenderer, "render", lambda *a, **k: pytest.fail("UNKNOWN was retried"))
    proof = render_variant(batch, variant["variant_id"], registry=registry, human_authorized=True)
    assert proof["state"] == "UNKNOWN" and proof["output"] is None and proof["full_decode_verified"] is False
    assert render_variant(batch, variant["variant_id"], registry=registry, human_authorized=True) == proof
    assert not registry.exists(variant["render"]["manifest_ref"])
    assert proof["manifest_sha256"] is None  # No manifest existed to prove.


def test_another_variants_valid_render_request_cannot_be_rebound(tmp_path, monkeypatch):
    registry, _, _, batch = create_case(tmp_path)
    a, b = batch["variants"]
    cp = b["composition"]
    other = CompositionStore(registry.root).load(cp["id"], cp["revision"])
    other_plan = composition_to_render_plan(other, composition_ref=cp["ref"])
    job_id = a["render"]["job_id"]
    request = checksum({"request_version": 1, "job_id": job_id, "created_at": "2026-10-30T00:00:00Z",
                        "plan": other_plan.to_dict(), "encoder": EncoderConfig().to_dict()})
    registry.put_immutable(f"render-jobs/{job_id}/request.json", canonical_bytes(request))
    monkeypatch.setattr(FFmpegRenderer, "render", lambda *a, **k: pytest.fail("cross-variant render was restarted"))
    with pytest.raises(CreativeScalingError, match="CROSS_VARIANT_RENDER"):
        render_variant(batch, a["variant_id"], registry=registry, human_authorized=True)
    assert not registry.exists(f"creative-scaling/renders/{a['variant_id'][12:]}.json")


def test_unsupported_local_render_settings_do_not_produce_a_receipt(tmp_path):
    registry, master, edits = master_case(tmp_path)
    from ai_commerce.composition import RenderSettings
    changed = create_composition(master.intent, canvas=master.canvas, tracks=master.tracks, media=master.media,
                                 source_brief=master.source_brief, settings=RenderSettings(fit="cover"))
    CompositionStore(registry.root).save(changed)
    batch = create_variant_batch(changed, edits, registry=registry, batch_id="unsupported-settings")
    with pytest.raises(CreativeScalingError, match="UNSUPPORTED_SETTINGS"):
        render_variant(batch, batch["variants"][0]["variant_id"], registry=registry, human_authorized=True)


def test_a06_delivered_master_is_consumed_without_modifying_its_delivery(tmp_path):
    images = []
    for i in range(2):
        path = tmp_path / f"e01-{i}.png"
        path.write_bytes(png(40 + i * 170))
        images.append(path)
    output = tmp_path / "e01-delivery"
    original = run_e01("A robot cat", images, output, width=32, height=48, fps=12, frames=3)
    manifest = inspect_e01(output)
    registry = ArtifactRegistry(output / "custody")
    cp = manifest["composition"]
    master = CompositionStore(registry.root).load(cp["id"], cp["revision"])
    ids = [m.artifact_id for m in master.media]
    edits = [{"mode": "CONTROLLED", "intent": label, "asset_order": ids[::order],
              "frames": [2, 4], "cost_entry_ids": []} for label, order in (("Garden opening", -1), ("Library opening", 1))]
    batch = create_variant_batch(master, edits, registry=registry, batch_id="e01-consumer")
    proof = render_variant(batch, batch["variants"][0]["variant_id"], registry=registry, human_authorized=True)
    assert proof["full_decode_verified"] is True
    assert verify_e01(output) == original


def test_published_schema_is_strict_versioned_and_accepts_real_batch(tmp_path):
    _, _, _, batch = create_case(tmp_path)
    path = Path(__file__).resolve().parents[1] / "contracts/CreativeVariantBatch.v1.schema.json"
    published = json.loads(path.read_text(encoding="utf-8"))
    assert published == schemas.schema(schemas.VARIANT_BATCH_SCHEMA)
    validator = Draft202012Validator(published)
    validator.validate(batch)
    for bad in ({}, {**batch, "publish": True}, {**batch, "schema_version": "CreativeVariantBatch.v2"}):
        assert not validator.is_valid(bad)
    for v in (float("nan"), float("inf")):
        with pytest.raises(CreativeScalingError, match="NONFINITE"):
            schemas.digest({"value": v})
    assert schemas.digest({"ab": "c"}) != schemas.digest({"a": "bc"})
    assert schemas.digest({"value": None}) != schemas.digest({"value": 0})
    assert schemas.digest({"value": None}) != schemas.digest({})


@pytest.mark.parametrize("mutation", ["manifest-pair", "render-state", "provider", "unknown-money", "charged-proof"])
def test_standalone_schema_rejects_inconsistent_states(tmp_path, mutation):
    registry, master, edits = master_case(tmp_path)
    cost = cost_entry(registry, 0)
    edits[0]["cost_entry_ids"] = [cost.entry_id]
    batch = create_variant_batch(master, edits, registry=registry, batch_id="structural")
    if mutation == "manifest-pair": batch["variants"][0]["assets"][0]["manifest_sha256"] = None
    elif mutation == "render-state": batch["variants"][0]["render"]["state"] = "ASSET_REQUIRED"
    elif mutation == "provider": batch["variants"][0]["assets"][0]["provenance"] = "PROVIDER_ATTESTED"
    elif mutation == "unknown-money": batch["variants"][0]["costs"][0]["status"] = "UNKNOWN"
    elif mutation == "charged-proof": batch["variants"][0]["costs"][0]["status"] = "CHARGED"
    assert not Draft202012Validator(schemas.schema(schemas.VARIANT_BATCH_SCHEMA)).is_valid(batch)


def test_json_bounds_and_private_fields_fail_closed(tmp_path):
    value = {}
    for _ in range(34):
        value = {"nested": value}
    with pytest.raises(CreativeScalingError, match="COMPLEXITY_LIMIT"):
        schemas.bounded(value)
    with pytest.raises(CreativeScalingError, match="TEXT_LIMIT"):
        schemas.bounded({"text": "x" * 16385})
    registry, master, edits = master_case(tmp_path, content_only=True)
    edits[0]["intent"] = "Contact person@example.test"
    with pytest.raises(CreativeScalingError, match="PRIVATE_DATA_NOT_ALLOWED"):
        create_variant_batch(master, edits, registry=registry, batch_id="private")
    with pytest.raises(CreativeScalingError, match="PRIVATE_DATA_NOT_ALLOWED"):
        schemas.private_text({"api_key": "credential-fixture"})
