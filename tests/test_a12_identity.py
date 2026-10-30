"""A12-02 source namespaces, raw-preserving replay, conflicts and corrections."""
from __future__ import annotations

import json
import socket
from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest

from ai_commerce.product import ProductError, build_identity, build_variant, prepare_bytes
from ai_commerce.product.evidence import subject_for
from ai_commerce.product.history import IdentityHistory
from ai_commerce.product import history as history_module

CAPTURE = "2026-10-30T05:00:00Z"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A12-02 must remain offline")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def snapshot(*, capture=CAPTURE, source_ref="source-a", bom=False, **changes):
    row = {"marketplace": "market-a", "seller_key": "seller-a", "listing_key": "listing-a",
           "product_key": "product-a", "variant_key": "red", "sku": "0001", "gtin": "claimed-gtin",
           "variant_attributes": {"color": "red"}, **changes}
    raw = json.dumps({"schema_version": "ManualProductListings.v1", "rows": [row]},
                     ensure_ascii=False).encode("utf-8")
    raw = b"\xef\xbb\xbf" + raw + b"\r\n" if bom else raw
    return prepare_bytes(raw, file_format="json", source_ref=source_ref, captured_at=capture, synthetic=True)


def test_stable_separate_namespaces_and_explicit_variant_identity():
    base = build_identity(marketplace="market-a", seller_key="seller-a", listing_key="listing-a", product_key="p")
    for field in ("seller_id", "listing_id", "product_id", "identity_id"):
        assert len(base[field].rsplit("-", 1)[-1]) == 64
    assert len({base[key] for key in ("seller_id", "listing_id", "product_id", "identity_id")}) == 4
    for market, seller in (("market-b", "seller-a"), ("market-a", "seller-b"), ("Market-a", "seller-a")):
        other = build_identity(marketplace=market, seller_key=seller, listing_key="listing-a", product_key="p")
        assert all(other[key] != base[key] for key in ("seller_id", "listing_id", "product_id"))
        assert build_variant(other, variant_key="red", gtin="same", sku="0001")["variant_id"] != build_variant(base, variant_key="red")["variant_id"]
    later_listing = build_identity(marketplace="market-a", seller_key="seller-a", listing_key="listing-b", product_key="p")
    assert later_listing["product_id"] == base["product_id"]
    assert later_listing["listing_id"] != base["listing_id"]
    red = build_variant(base, variant_key="red", sku="same", gtin="same")
    blue = build_variant(base, variant_key="blue", sku="same", gtin="same")
    assert red["variant_id"] != blue["variant_id"]
    changed_assertions = build_variant(base, variant_key="red", sku="changed", gtin="changed")
    assert changed_assertions["variant_id"] == red["variant_id"]
    assert changed_assertions["variant_record_id"] != red["variant_record_id"]
    assert base["canonical_product_id"] is None


def test_missing_product_and_variant_keys_are_never_inferred_from_gtin_sku_or_title():
    value = snapshot(product_key=None, variant_key=None, title="Verified identical product").to_dict()["listings"][0]
    assert value["identity"]["product_id"] is None
    assert value["variant"]["variant_id"] is None
    assert value["variant"]["asserted_gtin"] == "claimed-gtin"
    assert value["identity"]["canonical_product_id"] is None


def test_golden_source_pin_and_exact_replay_preserve_bom_crlf_raw():
    original = snapshot(bom=True)
    empty = IdentityHistory()
    first = empty.append(original)
    assert first.append(original) is first
    assert empty.snapshots() == ()
    assert first.snapshots() == (original,)
    assert first.snapshots()[0].raw_bytes().startswith(b"\xef\xbb\xbf")
    assert first.snapshots()[0].raw_bytes().endswith(b"\r\n")
    report = first.to_dict()
    assert report["revisions"][0]["revision_id"] == "a12-identity-revision-7ec9d855dca5b5209db6f0c63d7d0de865d3f189b9fb59a955e5cf962e942158"
    pin = report["observations"][0]["subject"]
    assert pin == subject_for(original, 1)
    assert pin["source_namespace"] == "market-a"
    assert pin["variant_key"] == "red"
    assert pin["snapshot_raw_sha256"] == sha256(original.raw_bytes()).hexdigest()
    assert report["canonical_resolution"] == "NOT_ATTEMPTED"
    assert report["conflicts"] == []
    report["observations"][0]["identity"]["asserted_keys"]["seller"] = "mutated"
    report["revisions"][0]["reason"] = "mutated"
    assert first.to_dict()["observations"][0]["identity"]["asserted_keys"]["seller"] == "seller-a"
    assert first.to_dict()["revisions"][0]["reason"] is None
    with pytest.raises(FrozenInstanceError):
        first._entries = ()


def test_cross_source_conflict_retains_all_keys_raw_and_no_merging():
    old = snapshot(bom=True)
    changed = snapshot(source_ref="source-b", product_key="p-else", sku="different", gtin="different",
                       variant_attributes={"color": "blue"})
    cross_merchant = snapshot(seller_key="seller-b")
    report = IdentityHistory().append(old).append(changed).append(cross_merchant).to_dict()
    assert len(report["observations"]) == 3
    assert len(report["conflicts"]) == 2
    listing, variant = report["conflicts"]
    assert listing["conflicting_fields"] == ["asserted_product_key"]
    assert variant["conflicting_fields"] == ["asserted_gtin", "asserted_sku", "attributes.color"]
    assert {pin["snapshot_id"] for pin in variant["source_refs"]} == {old.snapshot_id, changed.snapshot_id}
    assert all(item["identity"]["canonical_product_id"] is None for item in report["observations"])


def test_unknown_keys_are_retained_without_false_conflict():
    old = snapshot(product_key=None, sku=None, gtin=None, variant_attributes={})
    report = IdentityHistory().append(old).append(snapshot()).to_dict()
    assert len(report["observations"]) == 2
    assert report["conflicts"] == []


def test_correction_is_append_only_linear_and_exactly_replayable():
    old = snapshot(bom=True)
    new = snapshot(capture="2026-10-30T06:00:00Z", product_key="corrected", gtin="corrected")
    first = IdentityHistory().append(old)
    options = {"correction_of": old.snapshot_id, "reason": "Correct seller export", "actor_ref": "human-1"}
    revised = first.append(new, **options)
    assert revised.append(new, **options) is revised
    assert revised.snapshots() == (old, new)
    assert first.snapshots() == (old,)
    assert revised.snapshots()[0].raw_bytes() == old.raw_bytes()
    assert revised.to_dict()["revisions"][1]["correction_of"] == old.snapshot_id
    assert revised.to_dict()["revisions"][1]["operation"] == "CORRECTION"
    assert len(revised.to_dict()["conflicts"]) == 2
    with pytest.raises(ProductError, match="REPLAY_METADATA_CONFLICT"):
        revised.append(new)
    with pytest.raises(ProductError, match="CORRECTION_PARENT_NOT_CURRENT"):
        revised.append(snapshot(capture="2026-10-30T07:00:00Z", product_key="another"), **options)


@pytest.mark.parametrize("options,code", [
    ({"reason": "orphan"}, "CORRECTION_PARENT_REQUIRED"),
    ({"correction_of": "missing"}, "CORRECTION_REASON_REQUIRED"),
    ({"correction_of": "missing", "reason": "correction"}, "CORRECTION_ACTOR_REQUIRED"),
    ({"correction_of": "missing", "reason": "correction", "actor_ref": "human"}, "CORRECTION_PARENT_NOT_FOUND"),
    ({"recorded_at": "2026-10-30T04:59:59Z"}, "REVISION_BEFORE_CAPTURE"),
    ({"recorded_at": "2026-10-30T05:00:00"}, "INVALID_TIMESTAMP"),
])
def test_invalid_history_operations_leave_original_unchanged(options, code):
    first = IdentityHistory().append(snapshot())
    before = first.to_dict()
    with pytest.raises(ProductError, match=code):
        first.append(snapshot(product_key="corrected"), **options)
    assert first.to_dict() == before


@pytest.mark.parametrize("changes,code", [
    ({"source_ref": "another-source"}, "CORRECTION_SOURCE_MISMATCH"),
    ({"seller_key": "another-seller"}, "CORRECTION_SUBJECT_MISMATCH"),
    ({"marketplace": "another-market"}, "CORRECTION_SUBJECT_MISMATCH"),
    ({"listing_key": "another-listing"}, "CORRECTION_SUBJECT_MISMATCH"),
])
def test_correction_cannot_reassign_a_merchant_or_source(changes, code):
    old = snapshot()
    with pytest.raises(ProductError, match=code):
        IdentityHistory().append(old).append(snapshot(**changes), correction_of=old.snapshot_id,
                                              reason="fix", actor_ref="human")


def test_history_bounds_and_ordinal_types(monkeypatch):
    first = IdentityHistory().append(snapshot())
    monkeypatch.setattr(history_module, "MAX_HISTORY_SNAPSHOTS", 1)
    with pytest.raises(ProductError, match="IDENTITY_HISTORY_LIMIT"):
        first.append(snapshot(source_ref="source-b"))
    for ordinal in (True, False, 0, -1, 2, "1", None):
        with pytest.raises(ProductError, match="INVALID_LISTING_ORDINAL"):
            subject_for(snapshot(), ordinal)
    with pytest.raises(ProductError, match="LISTING_SNAPSHOT_REQUIRED"):
        IdentityHistory().append(snapshot().to_dict())


def test_history_parses_each_snapshot_once_for_many_listing_rows(monkeypatch):
    rows = [{"marketplace": "fixture", "seller_key": "seller", "listing_key": str(index),
             "variant_key": "one"} for index in range(100)]
    raw = json.dumps({"schema_version": "ManualProductListings.v1", "rows": rows}).encode()
    original = prepare_bytes(raw, file_format="json", source_ref="many-rows", captured_at=CAPTURE)
    value = IdentityHistory().append(original)
    from ai_commerce.product import ProductListingSnapshot
    real_to_dict = ProductListingSnapshot.to_dict
    calls = []

    def counted(self):
        calls.append(self)
        return real_to_dict(self)

    monkeypatch.setattr(ProductListingSnapshot, "to_dict", counted)
    assert len(value.to_dict()["observations"]) == 100
    assert len(calls) == 1


def test_projection_bound_failure_is_atomic(monkeypatch):
    original = snapshot()
    changed = snapshot(sku="different")
    value = IdentityHistory().append(original)
    before = value.to_dict()
    from ai_commerce.product import core
    with monkeypatch.context() as bounded:
        bounded.setattr(core, "MAX_NODES", 300)
        with pytest.raises(ProductError, match="DOCUMENT_COMPLEXITY_LIMIT"):
            value.append(changed)
    assert value.to_dict() == before
