"""AC06 append-only raw provenance, fan-in replay, concurrency and failures."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
from pathlib import Path
import socket
import sqlite3
from threading import Barrier

import pytest

from ai_commerce.creative.briefs import intake_brief
from ai_commerce.product import ProductError, prepare_bytes
from ai_commerce.product.claims import build_claim, review_claim
from ai_commerce.product.offers import build_offer
from ai_commerce.product.registry import EvidenceEvent, ProductEvidenceRegistry
from ai_commerce.product import registry as registry_module
from ai_commerce.product.rights import assess_rights, build_rights, review_rights
from ai_commerce.product.schemas import MAX_RAW_BYTES

CAPTURE = "2026-10-30T05:00:00Z"
RECORDED = "2026-10-30T07:00:00Z"
LATER = "2026-10-30T08:00:00Z"
ASSET = b"offline fixture media; metadata journal never stores this blob"
ASSET_HASH = sha256(ASSET).hexdigest()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A12 registry must remain local and offline")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def snapshot(*, synthetic=False, file_format="json", source_ref="listing-export", **changes):
    row = {"marketplace": "fixture", "seller_key": "merchant", "listing_key": "item",
           "variant_key": "red", "title": "Original source title", **changes}
    if file_format == "csv":
        raw = (",".join(row) + "\r\n" + ",".join(row.values()) + "\r\n").encode()
    else:
        raw = b"\xef\xbb\xbf" + json.dumps({"schema_version": "ManualProductListings.v1", "rows": [row]}).encode() + b"\r\n"
    return prepare_bytes(raw, file_format=file_format, source_ref=source_ref,
                         captured_at=CAPTURE, synthetic=synthetic)


def claim(*, listing=None, source_ref="claim-export", statement="Seller says waterproof", value=True,
          role="SUPPORTING", claim_key="waterproof"):
    raw = json.dumps({"schema_version": "ProductClaimSource.v1", "claim_key": claim_key,
                      "category": "PERFORMANCE", "statement": statement, "value": value, "unit": None,
                      "origin": "SELLER", "role": role, "observed_at": "2026-10-30T04:00:00Z"}).encode()
    return build_claim(snapshot() if listing is None else listing, ordinal=1, raw=raw,
                       source_ref=source_ref, captured_at=CAPTURE)


def offer(*, listing=None, amount="0"):
    return build_offer(snapshot() if listing is None else listing, ordinal=1,
                       raw=json.dumps({"schema_version": "ProductOfferSource.v1",
                                       "observed_at": "2026-10-30T04:00:00Z", "price_amount": amount,
                                       "currency": "VND"}).encode(),
                       source_ref="offer-export", captured_at=CAPTURE)


def rights(*, listing=None, declared="GRANTED"):
    raw = json.dumps({"schema_version": "ProductAssetRightsSource.v1", "asset": {
        "asset_ref": "https://invalid.test/image.jpg", "content_sha256": ASSET_HASH,
        "media_type": "IMAGE", "origin": "CREATOR_SUPPLIED", "origin_ref": "holder-export",
        "copyright_holder_ref": "holder-alias", "claimant_ref": "merchant-alias", "license_label": "CC-BY"},
        "permissions": [{"purpose": purpose, "declared_status": declared, "license_ref": "local-license",
                         "scope": {"territories": ["VN"], "media_types": ["IMAGE"], "channels": ["social-ad"],
                                   "valid_from": "2026-10-01T00:00:00Z", "valid_until": "2026-11-01T00:00:00Z"}}
                        for purpose in ("COMMERCIAL_USE", "ADVERTISEMENT", "CREATIVE_DERIVATIVE")]}).encode()
    return build_rights(snapshot() if listing is None else listing, ordinal=1, raw=raw,
                        source_ref="rights-export", captured_at=CAPTURE)


def reviewed_rights(item=None, *, all_purposes=False):
    item = rights() if item is None else item
    purposes = ("COMMERCIAL_USE", "ADVERTISEMENT", "CREATIVE_DERIVATIVE") if all_purposes else ("COMMERCIAL_USE",)
    for purpose in purposes:
        permission = next(p for p in item.to_dict()["permissions"] if p["purpose"] == purpose)
        raw = b"\xef\xbb\xbf" + json.dumps({"schema_version": "ProductAssetLicenseEvidence.v1",
            "instrument_kind": "LICENSE_INSTRUMENT", "asset_sha256": ASSET_HASH,
            "copyright_holder_ref": "holder-alias", "issuer_ref": "holder-alias",
            "authority_basis": "COPYRIGHT_HOLDER", "license_ref": "local-license",
            "issued_at": "2026-10-01T00:00:00Z", "purpose": purpose, "scope": permission["scope"],
            "conclusion": "ALLOWS_EXACT_SCOPED_USE"}).encode() + b"\r\n"
        item = review_rights(item, purpose=purpose, license_raw=raw, reviewer_ref="reviewer-alias",
                             reviewed_at="2026-10-30T06:00:00Z", method="Explicit exact use review",
                             evidence_ref="holder-license-export")
    return item


@pytest.fixture
def ledger(tmp_path):
    return ProductEvidenceRegistry("product.sqlite", root=tmp_path)


def append(ledger, record=None, **changes):
    return ledger.append(snapshot() if record is None else record,
                          **{"stream_key": "listing", "recorded_at": RECORDED, **changes})


def revise(ledger, record, parent, **changes):
    return append(ledger, record, **{"parent_event_id": parent.event_id, "operation": "CORRECTION",
                                    "actor_ref": "operator-alias", "reason_ref": "corrected-export",
                                    "recorded_at": LATER, **changes})


def counts(path):
    with sqlite3.connect(path) as db:
        return tuple(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("a12_events", "a12_raw"))


@pytest.mark.parametrize("factory", [snapshot, claim, offer, rights, reviewed_rights])
def test_exact_raw_reopen_and_idempotency_for_every_supported_contract(ledger, tmp_path, factory):
    record = factory()
    first = append(ledger, record)
    assert not first["duplicate"]
    assert append(ledger, record)["duplicate"]
    event = first["event"]
    assert event.evidence() == record
    assert record.raw_bytes() in event.raw_blobs().values()
    assert event.to_dict()["revision"] == 1
    reopened = ProductEvidenceRegistry(tmp_path / "product.sqlite", root=tmp_path)
    assert reopened.get(event.event_id) == event
    assert reopened.history("listing") == (event,)
    check = reopened.integrity_check()
    assert check["event_count"] == check["stream_count"] == 1
    assert check["integrity"] == "LOCAL_HASH_AND_REPLAY_VERIFIED"
    assert check["external_attestation"] == "NOT_ESTABLISHED"
    detached = event.raw_blobs()
    detached.clear()
    value = event.to_dict()
    value["evidence"]["record"].clear()
    assert event.evidence() == record and event.raw_blobs()
    with pytest.raises(FrozenInstanceError):
        event._encoded = b"changed"


def test_append_only_corrections_keep_original_source_and_full_lineage(ledger):
    original = snapshot()
    first = append(ledger, original)["event"]
    corrected = snapshot(title="Corrected source title")
    second = revise(ledger, corrected, first)["event"]
    third = revise(ledger, snapshot(title="Third source title"), second, operation="REVISION")["event"]
    history = ledger.history("listing")
    assert history == (first, second, third)
    assert [item.to_dict()["revision"] for item in history] == [1, 2, 3]
    assert second.to_dict()["parent_event_id"] == first.event_id
    assert third.to_dict()["parent_event_id"] == second.event_id
    assert ledger.get(first.event_id).evidence().raw_bytes() == original.raw_bytes()
    assert revise(ledger, corrected, first)["duplicate"]
    with pytest.raises(ProductError, match="REGISTRY_LINEAGE_CONFLICT"):
        revise(ledger, snapshot(title="Competing edit"), first)
    assert len(ledger.events()) == 3


def test_negative_claim_and_restricted_rights_are_retained_without_promotion(ledger):
    positive = append(ledger, claim(), stream_key="claim")["event"]
    negative = revise(ledger, claim(value=False, role="COUNTEREVIDENCE"), positive,
                       stream_key="claim", operation="COUNTEREVIDENCE")["event"]
    assert negative.evidence().to_dict()["claim"]["role"] == "COUNTEREVIDENCE"
    assert positive.evidence().to_dict()["status"] == "SELLER_ASSERTED"
    first = append(ledger, rights(), stream_key="rights")["event"]
    reviewed = revise(ledger, reviewed_rights(), first, stream_key="rights", operation="REVISION")["event"]
    restricted = revise(ledger, rights(declared="RESTRICTED"), reviewed,
                         stream_key="rights", operation="COUNTEREVIDENCE")["event"]
    assert {p["status"] for p in restricted.evidence().to_dict()["permissions"]} == {"RESTRICTED"}
    assert len(ledger.history("rights")) == 3
    assert len(ledger.history("claim")) == 2


def test_current_restricted_evidence_does_not_disappear_on_reopen_or_replay(ledger, tmp_path):
    original = append(ledger, reviewed_rights(), stream_key="rights")["event"]
    revise(ledger, rights(declared="RESTRICTED"), original, stream_key="rights", operation="COUNTEREVIDENCE")
    target = ProductEvidenceRegistry("target.sqlite", root=tmp_path)
    target.replay(ledger.events())
    history = target.history("rights")
    assert history[0].evidence().license_bytes()
    assert {p["status"] for p in history[-1].evidence().to_dict()["permissions"]} == {"RESTRICTED"}
    assert target.integrity_check() == ledger.integrity_check()


def test_all_upstream_components_fan_in_with_raw_evidence_and_freeform_creative(ledger, tmp_path):
    listing = snapshot()
    listing_revision = snapshot(title="Revised title")
    first = append(ledger, listing)["event"]
    revise(ledger, listing_revision, first)
    for stream, record in (("claim", claim(listing=listing_revision)), ("offer", offer(listing=listing_revision)),
                           ("rights", reviewed_rights(rights(listing=listing_revision), all_purposes=True))):
        append(ledger, record, stream_key=stream)
    source_events = ledger.events()
    target = ProductEvidenceRegistry("replayed.sqlite", root=tmp_path)
    results = target.replay(source_events)
    assert all(not result["duplicate"] for result in results)
    assert all(result["duplicate"] for result in target.replay(source_events))
    assert target.events() == source_events
    assert target.integrity_check() == ledger.integrity_check()
    assert counts(tmp_path / "product.sqlite")[0] == 5
    assert counts(tmp_path / "replayed.sqlite")[0] == 5
    restored = target.history("rights")[0].evidence()
    for purpose in ("COMMERCIAL_USE", "ADVERTISEMENT", "CREATIVE_DERIVATIVE"):
        assert assess_rights(restored, purpose=purpose, territory="VN", media_type="IMAGE", channel="social-ad",
                              as_of=RECORDED, asset_raw=ASSET)["status"] == "GRANTED"
    assert ASSET not in target.history("rights")[0].raw_blobs().values()
    assert intake_brief("A freeform story about a quiet morning")["product_ref"] is None


def test_deterministic_event_identity_and_replay_chain_without_wall_clock(ledger, tmp_path):
    other = ProductEvidenceRegistry("other.sqlite", root=tmp_path)
    one = append(ledger, claim(), stream_key="claim")["event"]
    two = append(other, claim(), stream_key="claim")["event"]
    assert one.event_id == two.event_id and one.to_bytes() == two.to_bytes()
    assert ledger.integrity_check() == other.integrity_check()
    assert EvidenceEvent.from_bytes(one.to_bytes(), raw_blobs=one.raw_blobs()) == one
    assert len(one.event_id) == len("a12-event-") + 64


def test_partial_replay_is_idempotent_and_out_of_order_lineage_rolls_back(ledger, tmp_path):
    first = append(ledger)["event"]
    second = revise(ledger, snapshot(title="Changed title"), first)["event"]
    target = ProductEvidenceRegistry("target.sqlite", root=tmp_path)
    target.initialize()
    with pytest.raises(ProductError, match="REGISTRY_LINEAGE_CONFLICT"):
        target.replay([second])
    assert target.events() == ()
    target.replay([first])
    result = target.replay([first, second])
    assert [item["duplicate"] for item in result] == [True, False]
    assert target.integrity_check() == ledger.integrity_check()


def test_replay_batch_malformed_second_event_rolls_back_first_and_raw_blobs(ledger, tmp_path):
    first = append(ledger)["event"]
    target = ProductEvidenceRegistry("target.sqlite", root=tmp_path)
    target.initialize()
    for bad in (object(), None):
        with pytest.raises(ProductError, match="INVALID_REGISTRY_EVENT"):
            target.replay([first, bad])
        assert target.events() == ()
        assert counts(tmp_path / "target.sqlite") == (0, 0)
    malformed = object.__new__(EvidenceEvent)
    object.__setattr__(malformed, "_encoded", b'{"invalid":NaN}')
    object.__setattr__(malformed, "_blobs", ())
    with pytest.raises(ProductError, match="JSON_NONFINITE"):
        target.replay([first, malformed])
    assert counts(tmp_path / "target.sqlite") == (0, 0)


def test_sqlite_insertion_failure_after_new_blob_rolls_back(ledger, tmp_path, monkeypatch):
    append(ledger)
    before = ledger.integrity_check()
    original_hash = registry_module._chain_hash
    with monkeypatch.context() as patch:
        patch.setattr(registry_module, "_chain_hash",
                      lambda seq, previous, payload: None if seq == 2 else original_hash(seq, previous, payload))
        with pytest.raises(ProductError, match="REGISTRY_IO_ERROR"):
            append(ledger, claim(), stream_key="claim")
    assert ledger.integrity_check() == before
    assert counts(tmp_path / "product.sqlite") == (1, 1)
    assert not append(ledger, claim(), stream_key="claim")["duplicate"]


def test_exception_after_full_insert_before_commit_also_rolls_back(ledger, tmp_path, monkeypatch):
    ledger.initialize()
    insert = registry_module.ProductEvidenceRegistry._insert
    def fail_after_insert(*args):
        insert(*args)
        raise RuntimeError("injected process boundary failure")
    with monkeypatch.context() as patch:
        patch.setattr(registry_module.ProductEvidenceRegistry, "_insert", staticmethod(fail_after_insert))
        with pytest.raises(RuntimeError, match="injected"):
            append(ledger)
    assert counts(tmp_path / "product.sqlite") == (0, 0)
    assert not append(ledger)["duplicate"]


def test_event_id_collision_rejects_distinct_evidence_without_writing(ledger, monkeypatch):
    first = append(ledger)["event"]
    before = ledger.integrity_check()
    with monkeypatch.context() as patch:
        patch.setattr(registry_module, "_event_id", lambda body: first.event_id)
        with pytest.raises(ProductError, match="REGISTRY_EVENT_COLLISION"):
            append(ledger, snapshot(title="A different source record"), stream_key="other")
    assert ledger.integrity_check() == before


def test_raw_hash_collision_is_rejected_in_the_bundle_before_sqlite(ledger, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(registry_module, "sha256_bytes", lambda raw: "f" * 64)
        with pytest.raises(ProductError, match="REGISTRY_RAW_COLLISION"):
            append(ledger, claim())


@pytest.mark.parametrize("count", [2, 6])
def test_concurrent_identical_writers_have_one_event_and_exact_duplicates(tmp_path, count):
    barrier = Barrier(count)
    record = claim()
    def work(_):
        own = ProductEvidenceRegistry("shared.sqlite", root=tmp_path)
        barrier.wait(timeout=10)
        return append(own, record, stream_key="claim")
    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(work, range(count)))
    assert sum(not result["duplicate"] for result in results) == 1
    assert len({result["event"].event_id for result in results}) == 1
    check = ProductEvidenceRegistry("shared.sqlite", root=tmp_path).integrity_check()
    assert check["event_count"] == 1


def test_concurrent_distinct_streams_serialize_and_replay_the_observed_order(tmp_path):
    count, barrier = 6, Barrier(6)
    def work(index):
        record = claim(statement=f"Source observation {index}", value=bool(index % 2))
        own = ProductEvidenceRegistry("shared.sqlite", root=tmp_path)
        barrier.wait(timeout=10)
        return append(own, record, stream_key=f"claim-{index}")
    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(work, range(count)))
    source = ProductEvidenceRegistry("shared.sqlite", root=tmp_path)
    target = ProductEvidenceRegistry("target.sqlite", root=tmp_path)
    assert all(not result["duplicate"] for result in results)
    assert source.integrity_check()["event_count"] == count
    target.replay(source.events())
    assert target.integrity_check() == source.integrity_check()


def test_concurrent_corrections_cannot_fork_or_overwrite_a_stream(ledger, tmp_path):
    first = append(ledger)["event"]
    barrier = Barrier(2)
    def work(index):
        own = ProductEvidenceRegistry("product.sqlite", root=tmp_path)
        record = snapshot(title=f"Competing correction {index}")
        barrier.wait(timeout=10)
        try:
            return revise(own, record, first)
        except ProductError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(work, range(2)))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert results.count("REGISTRY_LINEAGE_CONFLICT") == 1
    assert len(ledger.history("listing")) == 2


def test_lock_timeout_is_bounded_diagnostic_and_does_not_damage_existing_data(ledger, tmp_path):
    append(ledger)
    before = ledger.integrity_check()
    locked = sqlite3.connect(tmp_path / "product.sqlite", isolation_level=None)
    try:
        locked.execute("BEGIN IMMEDIATE")
        impatient = ProductEvidenceRegistry("product.sqlite", root=tmp_path, timeout_seconds=0.02)
        with pytest.raises(ProductError, match="REGISTRY_BUSY"):
            append(impatient, claim(), stream_key="claim")
    finally:
        locked.rollback()
        locked.close()
    assert ledger.integrity_check() == before
    append(ledger, claim(), stream_key="claim")


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_transient_sqlite_sidecar_disappearance_is_normal_concurrent_io(ledger, tmp_path, monkeypatch, suffix):
    ledger.initialize()
    sidecar = tmp_path / ("product.sqlite" + suffix)
    sidecar.write_bytes(b"")
    inspect = registry_module._inspect
    observed = []
    def disappear(path):
        if path == sidecar and not observed:
            observed.append(path.name)
            sidecar.unlink()  # Simulate the other SQLite connection's commit.
        return inspect(path)
    with monkeypatch.context() as patch:
        patch.setattr(registry_module, "_inspect", disappear)
        assert not append(ledger)["duplicate"]
    assert observed == [sidecar.name]
    assert ledger.integrity_check()["event_count"] == 1


def test_sidecar_directory_is_rejected_and_never_treated_as_absence(ledger, tmp_path):
    ledger.initialize()
    (tmp_path / "product.sqlite-journal").mkdir()
    with pytest.raises(ProductError, match="REGISTRY_FILE_NOT_PRIVATE_REGULAR"):
        append(ledger)


def test_personal_key_in_unmapped_source_is_rejected_without_persistence(ledger):
    raw = json.dumps({"rows": [{"market": "fixture", "seller": "merchant", "item": "item",
                               "full_name": "Personal name must not be archived"}]}).encode()
    item = prepare_bytes(raw, file_format="json", source_ref="preexport", captured_at=CAPTURE,
                         json_rows_pointer="/rows", field_map={"marketplace": "market", "seller_key": "seller",
                                                               "listing_key": "item"})
    with pytest.raises(ProductError, match="EVIDENCE_PERSONAL_DATA_NOT_ALLOWED"):
        append(ledger, item)


@pytest.mark.parametrize("table", ["a12_raw", "a12_events"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_sqlite_triggers_reject_source_mutations(ledger, tmp_path, table, operation):
    append(ledger)
    before = ledger.integrity_check()
    statement = f"DELETE FROM {table}" if operation == "DELETE" else (
        f"UPDATE {table} SET payload=payload")
    with sqlite3.connect(tmp_path / "product.sqlite") as db:
        with pytest.raises(sqlite3.IntegrityError, match="A12_APPEND_ONLY"):
            db.execute(statement)
    assert ledger.integrity_check() == before


def test_controlled_connection_denies_alter_and_direct_external_insert_is_rejected(ledger, tmp_path):
    ledger.initialize()
    with ledger._connect(write=True) as db:
        with pytest.raises(sqlite3.DatabaseError, match="authorized"):
            db.execute("ALTER TABLE a12_events ADD COLUMN tampered TEXT")
    with sqlite3.connect(tmp_path / "product.sqlite") as db:
        with pytest.raises(sqlite3.OperationalError, match="a12_append_allowed"):
            db.execute("INSERT INTO a12_raw VALUES(?,?,?)", (sha256(b"{}").hexdigest(), 2, b"{}"))
    assert ledger.events() == ()


def test_dropped_guard_is_detected_before_any_read_or_append(ledger, tmp_path):
    append(ledger)
    with sqlite3.connect(tmp_path / "product.sqlite") as db:
        db.execute("DROP TRIGGER a12_raw_no_delete")
    for action in (ledger.integrity_check, ledger.events, lambda: append(ledger, claim(), stream_key="claim")):
        with pytest.raises(ProductError, match="REGISTRY_SCHEMA_MISMATCH"):
            action()
    assert counts(tmp_path / "product.sqlite") == (1, 1)


def test_raw_and_chain_byte_corruption_are_detected_before_replay(ledger, tmp_path):
    record = snapshot()
    append(ledger, record)
    path = tmp_path / "product.sqlite"
    original = path.read_bytes()
    assert record.raw_bytes() in original
    corrupted = record.raw_bytes().replace(b"Original source title", b"Tampered source title")
    assert len(corrupted) == len(record.raw_bytes())
    path.write_bytes(original.replace(record.raw_bytes(), corrupted, 1))
    with pytest.raises(ProductError, match="REGISTRY_RAW_HASH_MISMATCH"):
        ledger.integrity_check()
    path.write_bytes(original)
    head = ledger.integrity_check()["head_hash"].encode()
    assert head in original
    path.write_bytes(original.replace(head, b"f" * 64))
    with pytest.raises(ProductError, match="REGISTRY_CHAIN_MISMATCH|REGISTRY_SQLITE_INTEGRITY_FAILED"):
        ledger.integrity_check()


def test_non_a12_database_is_rejected_without_changing_its_content(tmp_path):
    path = tmp_path / "other.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE foreign_ledger(value TEXT)")
        db.execute("PRAGMA application_id=1093742899")
    before = path.read_bytes()
    other = ProductEvidenceRegistry(path, root=tmp_path)
    with pytest.raises(ProductError, match="REGISTRY_FORMAT_MISMATCH"):
        append(other)
    assert path.read_bytes() == before


@pytest.mark.parametrize("factory", [
    lambda: snapshot(seller_key="other-merchant"), lambda: snapshot(variant_key="blue"),
    lambda: snapshot(synthetic=True), lambda: snapshot(source_ref="other-export"),
    lambda: claim(), lambda: offer(), lambda: rights(),
])
def test_correction_cannot_change_evidence_kind_or_listing_subject(ledger, factory):
    first = append(ledger)["event"]
    with pytest.raises(ProductError, match="REGISTRY_REVISION_SUBJECT_MISMATCH"):
        revise(ledger, factory(), first)
    assert len(ledger.events()) == 1


@pytest.mark.parametrize("factory", [lambda: claim(listing=snapshot(variant_key="blue")),
                                    lambda: claim(claim_key="another-property"),
                                    lambda: claim(listing=snapshot(seller_key="other"))])
def test_claim_corrections_cannot_change_claim_or_merchant_variant(ledger, factory):
    first = append(ledger, claim(), stream_key="claim")["event"]
    with pytest.raises(ProductError, match="REGISTRY_REVISION_SUBJECT_MISMATCH"):
        revise(ledger, factory(), first, stream_key="claim")


@pytest.mark.parametrize("changes,code", [
    ({"recorded_at": "2026-10-30T04:00:00Z"}, "REGISTRY_EVENT_BEFORE_EVIDENCE"),
    ({"recorded_at": "2026-10-30T00:00:00"}, "INVALID_REGISTRY_EVENT"),
    ({"recorded_at": "2026-02-30T05:00:00Z"}, "INVALID_TIMESTAMP"),
    ({"stream_key": "bad stream"}, "INVALID_REGISTRY_STREAM"),
    ({"operation": "PUBLISH"}, "INVALID_REGISTRY_EVENT"),
    ({"actor_ref": "operator-alias"}, "REGISTRY_LINEAGE_INVALID"),
    ({"operation": "REVISION"}, "REGISTRY_LINEAGE_INVALID"),
    ({"parent_event_id": "invalid"}, "INVALID_REGISTRY_EVENT_ID"),
    ({"parent_event_id": "a12-event-" + "f" * 64}, "REGISTRY_PARENT_NOT_FOUND"),
])
def test_invalid_event_metadata_is_rejected_atomically(ledger, changes, code):
    ledger.initialize()
    with pytest.raises(ProductError, match=code):
        append(ledger, **changes)
    assert ledger.events() == ()


def test_revision_must_change_record_include_reason_and_follow_parent_time(ledger):
    first = append(ledger)["event"]
    for changes, code in (({}, "REGISTRY_REVISION_UNCHANGED"),
                          ({"reason_ref": None}, "REGISTRY_LINEAGE_INVALID"),
                          ({"actor_ref": None}, "REGISTRY_LINEAGE_INVALID"),
                          ({"recorded_at": "2026-10-30T06:00:00Z"}, "REGISTRY_REVISION_BEFORE_PARENT")):
        record = snapshot() if not changes else snapshot(title="Changed")
        with pytest.raises(ProductError, match=code):
            revise(ledger, record, first, **changes)
    with pytest.raises(ProductError, match="REGISTRY_COUNTEREVIDENCE_REQUIRED"):
        revise(ledger, snapshot(title="Negative is not implicit"), first, operation="COUNTEREVIDENCE")


def test_event_time_must_follow_rights_and_independent_claim_review(ledger):
    item = reviewed_rights()
    with pytest.raises(ProductError, match="REGISTRY_EVENT_BEFORE_EVIDENCE"):
        append(ledger, item, recorded_at=CAPTURE)
    verified = review_claim(claim(), proof_raw=b'{"report":"Local separate report fixture"}',
                            reviewer_ref="reviewer-alias", producer_ref="lab-alias", method="Explicit report review",
                            reviewed_at="2026-10-30T06:00:00Z", evidence_ref="report-export",
                            evidence_kind="INDEPENDENT_TEST_REPORT")
    with pytest.raises(ProductError, match="REGISTRY_EVENT_BEFORE_EVIDENCE"):
        append(ledger, verified, recorded_at=CAPTURE)
    event = append(ledger, verified)["event"]
    assert event.evidence() == verified
    assert verified.proof_bytes() in event.raw_blobs().values()


def test_raw_non_json_report_is_rejected_by_storage_without_changing_a12_02_api(ledger):
    verified = review_claim(claim(), proof_raw=b"A valid in-memory report with no storage format",
                            reviewer_ref="reviewer-alias", producer_ref="lab-alias", method="Read local report",
                            reviewed_at="2026-10-30T06:00:00Z", evidence_ref="report-export",
                            evidence_kind="INDEPENDENT_TEST_REPORT")
    assert verified.to_dict()["status"] == "INDEPENDENTLY_VERIFIED_ONLY_WITH_REAL_EVIDENCE"
    with pytest.raises(ProductError, match="JSON_MALFORMED"):
        append(ledger, verified)


@pytest.mark.parametrize("text", ["person@example.test", "+84 912 345 678", "Bearer abcdefghijklmnop",
                                  "password=never-store", "sk-abcdefghijklmnopqrstuvwx",
                                  "https://invalid.test/path?access_token=sensitive",
                                  "https://person:password@invalid.test/path", "C:\\Users\\Someone\\file.json",
                                  "ｐｅｒｓｏｎ＠ｅｘａｍｐｌｅ．ｔｅｓｔ", "person%40example.test"])
def test_privacy_rejects_source_or_claim_text_without_storing_it(ledger, tmp_path, text):
    ledger.initialize()
    for record in (snapshot(title=text), claim(statement=text), claim(source_ref=text)):
        with pytest.raises(ProductError) as error:
            append(ledger, record)
        assert text not in str(error.value)
        assert counts(tmp_path / "product.sqlite") == (0, 0)
    assert text.encode("utf-8") not in (tmp_path / "product.sqlite").read_bytes()


def test_unmapped_raw_fields_are_scanned_and_never_silently_redacted(ledger):
    raw = json.dumps({"rows": [{"market": "fixture", "seller": "merchant", "item": "item", "variant": "red",
                               "ignored": {"refresh_token": "must-never-be-archived"}}]}).encode()
    item = prepare_bytes(raw, file_format="json", source_ref="preexport", source_kind="PREEXPORTED_API",
                         captured_at=CAPTURE, json_rows_pointer="/rows",
                         field_map={"marketplace": "market", "seller_key": "seller", "listing_key": "item",
                                    "variant_key": "variant"})
    assert item.raw_bytes() == raw
    with pytest.raises(ProductError, match="EVIDENCE_SECRET_NOT_ALLOWED"):
        append(ledger, item)


def test_csv_raw_bytes_hashes_urls_and_numeric_product_identifiers_are_preserved(ledger):
    item = snapshot(file_format="csv", gtin="0123456789012", sku="123456789012",
                    listing_url="https://invalid.test/items/123456789012")
    event = append(ledger, item)["event"]
    assert event.evidence().raw_bytes() == item.raw_bytes()
    assert b"\r\n" in item.raw_bytes()
    assert event.evidence().to_dict()["listings"][0]["variant"]["asserted_gtin"] == "0123456789012"


def test_malformed_replay_event_ids_raw_references_and_untrusted_bounds(ledger):
    event = append(ledger)["event"]
    value = event.to_dict()
    value["event_id"] = "a12-event-" + "0" * 64
    with pytest.raises(ProductError, match="REGISTRY_EVENT_ID_MISMATCH"):
        EvidenceEvent(value, raw_blobs=event.raw_blobs())
    with pytest.raises(ProductError, match="REGISTRY_RAW_REFERENCES_MISMATCH"):
        EvidenceEvent(event.to_dict(), raw_blobs={})
    altered = event.raw_blobs()
    altered[next(iter(altered))] += b"changed"
    with pytest.raises(ProductError, match="REGISTRY_RAW_HASH_MISMATCH"):
        EvidenceEvent(event.to_dict(), raw_blobs=altered)
    altered[next(iter(altered))] = b"x" * (MAX_RAW_BYTES + 1)
    with pytest.raises(ProductError, match="REGISTRY_RAW_SIZE_INVALID"):
        EvidenceEvent(event.to_dict(), raw_blobs=altered)
    with pytest.raises(ProductError, match="REGISTRY_BATCH_LIMIT"):
        ledger.replay([event] * (registry_module.MAX_BATCH_EVENTS + 1))
    with pytest.raises(ProductError, match="INVALID_REGISTRY_RECORD"):
        append(ledger, event.to_dict())


def test_event_batch_and_database_limits_fail_without_partial_writes(ledger, tmp_path, monkeypatch):
    event = append(ledger)["event"]
    with monkeypatch.context() as patch:
        patch.setattr(registry_module, "MAX_EVENTS", 1)
        with pytest.raises(ProductError, match="REGISTRY_EVENT_LIMIT"):
            append(ledger, claim(), stream_key="claim")
    target = ProductEvidenceRegistry("target.sqlite", root=tmp_path)
    target.initialize()
    with monkeypatch.context() as patch:
        patch.setattr(registry_module, "MAX_BATCH_BYTES", len(event.to_bytes()))
        with pytest.raises(ProductError, match="REGISTRY_BATCH_SIZE_LIMIT"):
            target.replay([event])
    assert target.events() == ()
    with monkeypatch.context() as patch:
        patch.setattr(registry_module, "MAX_DATABASE_BYTES", 1)
        with pytest.raises(ProductError, match="REGISTRY_DATABASE_TOO_LARGE"):
            append(ledger, claim(), stream_key="claim")
    assert ledger.events() == (event,)


@pytest.mark.parametrize("path", ["../escape.sqlite", "https://invalid.test/a.sqlite", "//server/share/a.sqlite",
                                  "C:relative.sqlite", "file:database.sqlite", "CON.sqlite", "bad.txt"])
def test_local_storage_rejects_traversal_uri_network_devices_and_suffix(tmp_path, path):
    with pytest.raises(ProductError):
        ProductEvidenceRegistry(path, root=tmp_path).initialize()


def test_missing_database_is_never_created_by_read_and_unsafe_root_is_rejected(ledger, tmp_path):
    with pytest.raises(ProductError, match="REGISTRY_NOT_FOUND"):
        ledger.events()
    assert not (tmp_path / "product.sqlite").exists()
    outside = ProductEvidenceRegistry(tmp_path.parent / "outside.sqlite", root=tmp_path)
    with pytest.raises(ProductError, match="PATH_OUTSIDE_ROOT"):
        outside.initialize()
    for timeout in (-1, 6, float("nan"), True):
        with pytest.raises(ProductError, match="INVALID_REGISTRY_TIMEOUT"):
            ProductEvidenceRegistry("x.sqlite", root=tmp_path, timeout_seconds=timeout)


def test_hardlink_alias_cannot_be_used_as_a_registry(ledger, tmp_path):
    append(ledger)
    alias = tmp_path / "alias.sqlite"
    try:
        alias.hardlink_to(tmp_path / "product.sqlite")
    except OSError as error:
        pytest.skip(f"Native hardlinks unavailable: {type(error).__name__}")
    with pytest.raises(ProductError, match="REGISTRY_FILE_NOT_PRIVATE_REGULAR"):
        ProductEvidenceRegistry(alias, root=tmp_path).integrity_check()


def test_symlink_database_and_parent_are_rejected_when_native_links_are_available(ledger, tmp_path):
    append(ledger)
    alias = tmp_path / "linked.sqlite"
    try:
        alias.symlink_to(tmp_path / "product.sqlite")
    except OSError as error:
        pytest.skip(f"Native symlinks unavailable: {type(error).__name__}")
    with pytest.raises(ProductError, match="PATH_LINK_NOT_ALLOWED"):
        ProductEvidenceRegistry(alias, root=tmp_path).integrity_check()


@pytest.mark.parametrize("event_id", [None, "bad", "a12-event-" + "f" * 64])
def test_get_has_bounded_not_found_and_invalid_identity_errors(ledger, event_id):
    ledger.initialize()
    with pytest.raises(ProductError, match="INVALID_REGISTRY_EVENT_ID|REGISTRY_EVENT_NOT_FOUND"):
        ledger.get(event_id)
