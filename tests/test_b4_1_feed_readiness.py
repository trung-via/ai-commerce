"""B4.1a actual offline A12 consumer + negative rights/freshness tests."""
from __future__ import annotations

from hashlib import sha256
import socket

import pytest

from ai_commerce.product import ProductEvidenceRegistry, prepare_bytes
from ai_commerce.product.core import canonical_bytes
from ai_commerce.product.b4_feed_readiness import (
    B4FeedHold, B4FeedReadiness, LIMITS, review_local_product_feed,
)
from test_a12_registry import (
    CAPTURE, LATER, RECORDED, append, offer, rights, reviewed_rights, snapshot,
)

INSTANT = "2026-10-30T05:00:00Z"


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("B4.1a must not use a network")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def native(tmp_path, *, synthetic=False, rights_record=None, with_offer=True):
    reg = ProductEvidenceRegistry("product.sqlite", root=tmp_path)
    listing = snapshot(synthetic=synthetic)
    reg.append(listing, stream_key="listing", recorded_at=RECORDED)
    if with_offer:
        reg.append(offer(listing=listing), stream_key="offer", recorded_at=RECORDED)
    if rights_record is not None:
        reg.append(rights_record(listing), stream_key="rights", recorded_at=RECORDED)
    subject = listing.to_dict()["listings"][0]
    return (reg, subject["identity"]["listing_id"],
            subject["variant"]["variant_id"])


def run(reg, listing, variant, *, at=INSTANT, head=None, age=7200):
    expected = reg.integrity_check()["head_hash"] if head is None else head
    return review_local_product_feed(
        reg, listing_id=listing, variant_id=variant,
        as_of=at, max_age_seconds=age, expected_head_sha256=expected,
    ).to_dict()


def test_native_a12_current_offer_and_locally_reviewed_rights_still_no_live_authority(tmp_path):
    reg, listing, variant = native(
        tmp_path, rights_record=lambda snap: reviewed_rights(
            rights(listing=snap), all_purposes=True))
    original = reg.integrity_check()
    out = run(reg, listing, variant)
    assert out["status"] == "OFFLINE_ADVISORY_ONLY", out
    assert out["offer_assessment"]["status"] == "VALID_OBSERVATION"
    assert out["offer_assessment"]["active_offer_id"].startswith("a12-offer-")
    assert out["rights_status"] == "GRANTED"
    assert out["reason_codes"] == []
    assert out["limits"] == LIMITS
    assert out["limits"]["provider_entitlement"] == "UNVERIFIED"
    assert out["limits"]["license_authentication"] == "NOT_ESTABLISHED"
    assert run(reg, listing, variant) == out
    assert reg.integrity_check() == original
    assert len(reg.events()) == 3


def test_native_missing_rights_is_not_a_grant_or_zero(tmp_path):
    reg, listing, variant = native(tmp_path)
    out = run(reg, listing, variant)
    assert out["status"] == "LOCAL_EVIDENCE_HOLD"
    assert out["reason_codes"] == ["RIGHTS_UNKNOWN"]
    assert out["rights_status"] == "UNKNOWN"
    assert out["limits"]["rights_to_execute"] == "NONE"
    assert out["related_counterevidence_event_ids"] == []


def test_source_and_offer_staleness_preserve_owner_reason_separation(tmp_path):
    reg, listing, variant = native(tmp_path)
    out = run(reg, listing, variant, at="2026-11-10T05:00:00Z")
    assert "SOURCE_STALE" in out["reason_codes"]
    assert "OFFER_STALE" in out["reason_codes"]
    assert "RIGHTS_UNKNOWN" in out["reason_codes"]
    assert out["offer_assessment"]["status"] == "STALE"


def test_no_offer_and_synthetic_source_always_hold(tmp_path):
    reg, listing, variant = native(tmp_path, synthetic=True, with_offer=False)
    out = run(reg, listing, variant)
    assert "OFFER_NO_DATA" in out["reason_codes"]
    assert "SYNTHETIC_SOURCE" in out["reason_codes"]
    assert out["limits"]["provider_connection"] == "NOT_ATTEMPTED"


def test_append_only_native_rights_counterevidence_forces_review_not_revocation_claim(tmp_path):
    reg, listing, variant = native(
        tmp_path, rights_record=lambda snap: reviewed_rights(
            rights(listing=snap), all_purposes=True))
    initial = reg.history("rights")[0]
    reg.append(
        rights(listing=initial.evidence()._snapshot, declared="RESTRICTED"),
        stream_key="rights", recorded_at=LATER,
        parent_event_id=initial.event_id, operation="COUNTEREVIDENCE",
        actor_ref="operator-alias", reason_ref="rights-change",
    )
    out = run(reg, listing, variant)
    assert out["status"] == "LOCAL_EVIDENCE_HOLD"
    assert "COUNTEREVIDENCE_REVIEW_REQUIRED" in out["reason_codes"]
    assert "RIGHTS_RESTRICTED" in out["reason_codes"]
    assert len(out["related_counterevidence_event_ids"]) == 1
    assert out["limits"]["external_revocation"] == "NOT_VERIFIED"
    assert out["limits"]["cross_owner_asset_binding"] == "UNVERIFIED"


def test_exact_variant_and_independent_head_pin_fail_closed(tmp_path):
    reg, listing, variant = native(tmp_path)
    with pytest.raises(B4FeedHold, match="B41_REGISTRY_HEAD_CHANGED"):
        run(reg, listing, variant, head="f" * 64)
    with pytest.raises(B4FeedHold, match="B41_A12_NATIVE_READ_HOLD"):
        run(reg, listing, "a12-variant-" + "e" * 64)
    with pytest.raises(B4FeedHold, match="B41_EXACT_VARIANT_REQUIRED"):
        review_local_product_feed(
            reg, listing_id=listing, variant_id="", as_of=INSTANT,
            expected_head_sha256=reg.integrity_check()["head_hash"],
        )


def test_native_head_change_after_pinned_read_is_not_silently_accepted(tmp_path):
    reg, listing, variant = native(tmp_path)
    old_head = reg.integrity_check()["head_hash"]
    reg.append(offer(listing=snapshot(), amount="2"), stream_key="offer",
               recorded_at=LATER, parent_event_id=reg.history("offer")[-1].event_id,
               operation="CORRECTION", actor_ref="operator-alias", reason_ref="corrected-export")
    with pytest.raises(B4FeedHold, match="B41_REGISTRY_HEAD_CHANGED"):
        run(reg, listing, variant, head=old_head)


def test_packet_hash_forgery_and_false_provider_grant_rejected(tmp_path):
    reg, listing, variant = native(tmp_path)
    out = run(reg, listing, variant)
    out["limits"]["provider_entitlement"] = "VERIFIED"
    with pytest.raises(B4FeedHold, match="B41_PACKET_DIGEST_INVALID"):
        B4FeedReadiness(canonical_bytes(out))
    out["record_sha256"] = sha256(canonical_bytes({
        k: v for k, v in out.items() if k != "record_sha256",
    })).hexdigest()
    with pytest.raises(B4FeedHold, match="B41_PACKET_AUTHORITY_INVALID"):
        B4FeedReadiness(canonical_bytes(out))
