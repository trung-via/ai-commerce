"""Offline unit and compatibility tests for A04 -> PULSE handoff contract.

No network, provider, native Windows host, publish, or WakeHub side effects.
Does not modify existing A04 or PULSE contracts; tests compatibility strictly
through read-only imports.
"""
from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path
import pytest

from ai_commerce.a04_pulse_handoff import (
    HANDOFF_SCHEMA_VERSION,
    HANDOFF_STATUS,
    PULSE_RECEIVER_EXECUTION,
    A04PulseHandoffError,
    A04PulseHandoffPacket,
    build_a04_pulse_handoff_packet,
    canonical_handoff_bytes,
    check_unique_logical_identities,
    derive_logical_identity,
    parse_a04_pulse_handoff_packet,
    validate_a04_pulse_handoff_packet,
)
from ai_commerce.core.provenance import canonical_bytes as a04_canonical_bytes, sha256_bytes
from ai_commerce.media.generation import (
    A04_CREATIVE_CONSUMER_ID,
    A04_HANDOFF_RECEIPT_SCHEMA,
    A04CreativeHandoffReceipt,
)
from ai_commerce.production_runtime import attended_worker
from ai_commerce.production_runtime.contracts import (
    draft_authorization,
    parse_contract,
    seal_contract,
)
from ai_commerce.production_runtime.state_machine import (
    Event,
    EventKind,
    Journey,
    State,
    transition,
)

SOURCE_SHA = "388d445d2933e28af3a8a4583ab3e626f5bbfcea"
JOURNEY_ID = "journey:" + "11" * 16
CYCLE_ID = "cycle-2026-01"
JOB_ID = "job-a04-001"


def _hex(ch: str) -> str:
    return ch * 64


def _make_receipt(
    *,
    journey_id: str = JOURNEY_ID,
    cycle_id: str = CYCLE_ID,
    job_id: str = JOB_ID,
    attempt: int = 1,
    source_sha: str = SOURCE_SHA,
    revision: int = 1,
    parent_package_digest: str | None = None,
    package_bytes: bytes = b'{"package":"v1"}',
    a03_result_bytes: bytes = b'{"a03_result":"complete"}',
    audio_mode: str = "SILENCE",
) -> tuple[A04CreativeHandoffReceipt, bytes, bytes, bytes]:
    pkg_digest = sha256_bytes(package_bytes)
    res_digest = sha256_bytes(a03_result_bytes)
    receipt = A04CreativeHandoffReceipt.create(
        journey_id=journey_id,
        cycle_id=cycle_id,
        job_id=job_id,
        attempt=attempt,
        actor_ref="human:editor-01",
        job_actor_ref="human:operator-01",
        source_sha=source_sha,
        a03_result_ref="store/results/" + res_digest + ".json",
        a03_result_digest=res_digest,
        package_id="package:" + _hex("a"),
        revision=revision,
        parent_package_digest=parent_package_digest,
        package_ref="store/packages/rev-" + str(revision) + ".json",
        package_digest=pkg_digest,
        brief_id="brief-001",
        source_brief_digest=_hex("b"),
        selection_id="selection:" + _hex("c"),
        concept_id="concept:" + _hex("d"),
        concept_revision=1,
        concept_digest=_hex("e"),
        cycle_job_digest=_hex("1"),
        cycle_result_digest=_hex("2"),
        reasoning_receipt_digest=_hex("3"),
        audio_mode=audio_mode,
        scene_count=3,
        media_target_count=1,
        rights_status="EVIDENCE_BOUND",
    )
    return receipt, a04_canonical_bytes(receipt.to_dict()), package_bytes, a03_result_bytes


def _make_directive(
    *,
    journey_id: str = JOURNEY_ID,
    source_sha: str = SOURCE_SHA,
    directive_version: int = 1,
    product_ref: str | None = None,
    action_class: str = "EXPORT_DRAFT",
    action_classes: list[str] | None = None,
    allowed_tools: list[str] | None = None,
    scope_overrides: dict[str, object] | None = None,
    fixture_mode: bool = False,
    rights_status: str | None = None,
    asset_bytes: bytes = b"\x89PNG\r\n\x1a\nfake-offline-still-bytes",
    rights_bytes: bytes = b'{"schema_version":"LocalStillRights.v1","status":"GRANTED"}',
    asset_evidence_type: str | None = None,
    assets_override: list[dict[str, object]] | None = None,
):
    asset_sha = sha256_bytes(asset_bytes)
    asset_ref = "asset:" + asset_sha
    rights_sha = sha256_bytes(rights_bytes)
    rights_ref = "rights:" + rights_sha

    if fixture_mode:
        ev_type = asset_evidence_type or "SYNTHETIC_FIXTURE"
        actor = "FIXTURE"
        prov_ev = "SYNTHETIC_FIXTURE"
        r_status = rights_status if rights_status is not None else "NOT_APPLICABLE"
        rights_obj = {
            "status": r_status,
            "ref": None if r_status == "NOT_APPLICABLE" else rights_ref,
            "sha256": None if r_status == "NOT_APPLICABLE" else rights_sha,
        }
        actor_ref = "fixture:" + "22" * 16
        source_ref = "directive:" + "33" * 16
    else:
        ev_type = asset_evidence_type or "HUMAN_SUPPLIED"
        actor = "HUMAN"
        prov_ev = "HUMAN_SUPPLIED"
        r_status = rights_status if rights_status is not None else "GRANTED"
        rights_obj = {
            "status": r_status,
            "ref": rights_ref if r_status == "GRANTED" else None,
            "sha256": rights_sha if r_status == "GRANTED" else None,
        }
        actor_ref = "human:" + "44" * 16
        source_ref = "directive:" + "55" * 16

    scope = {
        "action_class": action_class,
        "provider_ref": None,
        "account_ref": None,
        "surface": None,
        "asset_sha256": None,
    }
    if scope_overrides:
        scope.update(scope_overrides)

    body = {
        "schema_version": "CreativeDirective.v1",
        "journey_id": journey_id,
        "source_sha": source_sha,
        "directive_version": directive_version,
        "scope": scope,
        "provenance": {
            "evidence_type": prov_ev,
            "actor": actor,
            "actor_ref": actor_ref,
            "source_ref": source_ref,
            "source_sha": source_sha,
        },
        "authorization": draft_authorization(),
        "creative": {
            "freeform": "Quiet minimal offline product showcase without CTA.",
            "product_ref": product_ref,
        },
        "assets": assets_override
        if assets_override is not None
        else [
            {
                "ref": asset_ref,
                "sha256": asset_sha,
                "evidence_type": ev_type,
                "rights": rights_obj,
            }
        ],
        "action_classes": action_classes if action_classes is not None else [action_class],
        "allowed_tools": allowed_tools if allowed_tools is not None else [attended_worker.TOOL],
    }
    contract = seal_contract("CreativeDirective", body)
    return contract, {asset_ref: asset_bytes}, ({} if fixture_mode else {rights_ref: rights_bytes})


def test_positive_deterministic_offline_handoff_silent_no_cta_null_product():
    receipt, receipt_raw, pkg_raw, res_raw = _make_receipt(audio_mode="SILENCE")
    directive, asset_map, rights_map = _make_directive(product_ref=None)

    pkt1 = build_a04_pulse_handoff_packet(
        a04_receipt=receipt,
        directive=directive,
        cta_mode="NONE",
        cta_text=None,
        directive_bytes=directive.raw,
        receipt_bytes=receipt_raw,
        package_bytes=pkg_raw,
        a03_result_bytes=res_raw,
        asset_bytes_by_ref=asset_map,
        rights_bytes_by_ref=rights_map,
        expected_source_sha=SOURCE_SHA,
        expected_journey_id=JOURNEY_ID,
        expected_cycle_id=CYCLE_ID,
        min_directive_version=1,
        min_package_revision=1,
    )
    pkt2 = build_a04_pulse_handoff_packet(
        a04_receipt=receipt,
        directive=directive,
        cta_mode="NONE",
        cta_text=None,
        directive_bytes=directive.raw,
        receipt_bytes=receipt_raw,
        package_bytes=pkg_raw,
        a03_result_bytes=res_raw,
        asset_bytes_by_ref=asset_map,
        rights_bytes_by_ref=rights_map,
    )

    assert pkt1 == pkt2
    raw_pkt = pkt1.to_canonical_bytes()
    assert raw_pkt == pkt2.to_canonical_bytes()
    assert pkt1.schema_version == HANDOFF_SCHEMA_VERSION
    assert pkt1.status == HANDOFF_STATUS
    assert pkt1.product_ref is None
    assert pkt1.audio_mode == "SILENCE"
    assert pkt1.cta_mode == "NONE" and pkt1.cta_text is None
    assert pkt1.authority.allow_network is False
    assert pkt1.authority.allow_spend is False
    assert pkt1.authority.allow_publish is False
    assert pkt1.authority.provider_entitlement is False
    assert pkt1.authority.spend_ceiling_minor == 0
    assert pkt1.authority.pulse_receiver_execution == PULSE_RECEIVER_EXECUTION
    assert pkt1.authority.wait_brain_claimed is False
    assert pkt1.authority.wakehub_effect == "NONE"

    parsed = parse_a04_pulse_handoff_packet(raw_pkt)
    assert parsed == pkt1

    res = validate_a04_pulse_handoff_packet(
        raw_pkt,
        a04_receipt=receipt_raw,
        directive=directive.raw,
        package_bytes=pkg_raw,
        a03_result_bytes=res_raw,
        asset_bytes_by_ref=asset_map,
        rights_bytes_by_ref=rights_map,
        expected_source_sha=SOURCE_SHA,
        expected_journey_id=JOURNEY_ID,
        expected_cycle_id=CYCLE_ID,
        expected_directive_version=1,
        expected_package_revision=1,
    )
    assert res.valid is True
    assert res.status == HANDOFF_STATUS
    assert res.pulse_receiver_execution == "NOT_ATTEMPTED"
    assert res.wait_brain_claimed is False
    assert set(res.bytes_verified) == {
        "a03_result_bytes",
        "asset_bytes",
        "directive_bytes",
        "package_bytes",
        "packet_bytes",
        "receipt_bytes",
        "rights_bytes",
    }

    # Omitting optional raw bytes still validates contract but honestly reports no byte verification.
    res_no_raw = validate_a04_pulse_handoff_packet(
        pkt1,
        a04_receipt=receipt,
        directive=directive,
    )
    assert res_no_raw.valid is True
    assert res_no_raw.bytes_verified == ()


def test_positive_fixture_mode_revision2_and_explicit_product_cta():
    parent_digest = _hex("f")
    receipt, receipt_raw, pkg_raw, res_raw = _make_receipt(
        revision=2,
        parent_package_digest=parent_digest,
        audio_mode="AMBIENT_ONLY",
    )
    directive, asset_map, _ = _make_directive(
        fixture_mode=True,
        directive_version=3,
        product_ref="product:" + _hex("9"),
        action_class="PLAN_OFFLINE",
    )
    pkt = build_a04_pulse_handoff_packet(
        a04_receipt=receipt,
        directive=directive,
        cta_mode="EXPLICIT",
        cta_text="Learn more",
        directive_bytes=directive.raw,
        receipt_bytes=receipt_raw,
        package_bytes=pkg_raw,
        a03_result_bytes=res_raw,
        asset_bytes_by_ref=asset_map,
        rights_bytes_by_ref={},
        min_directive_version=3,
        min_package_revision=2,
    )
    assert pkt.package_revision == 2
    assert pkt.parent_package_digest == parent_digest
    assert pkt.product_ref == "product:" + _hex("9")
    assert pkt.cta_mode == "EXPLICIT" and pkt.cta_text == "Learn more"
    assert pkt.assets[0].evidence_type == "SYNTHETIC_FIXTURE"
    assert pkt.assets[0].rights_status == "NOT_APPLICABLE"


def test_consumer_contract_compatibility_with_readonly_a04_and_pulse_contracts():
    receipt, receipt_raw, pkg_raw, res_raw = _make_receipt(audio_mode="SILENCE")
    directive, asset_map, rights_map = _make_directive(
        product_ref=None,
        action_class="EXPORT_DRAFT",
        allowed_tools=[attended_worker.TOOL],
    )
    pkt = build_a04_pulse_handoff_packet(
        a04_receipt=receipt,
        directive=directive,
        directive_bytes=directive.raw,
        receipt_bytes=receipt_raw,
        package_bytes=pkg_raw,
        a03_result_bytes=res_raw,
        asset_bytes_by_ref=asset_map,
        rights_bytes_by_ref=rights_map,
    )

    # Read-only A04 contract compatibility
    reloaded_receipt = A04CreativeHandoffReceipt.from_dict(receipt.to_dict())
    assert reloaded_receipt.schema_version == A04_HANDOFF_RECEIPT_SCHEMA
    assert reloaded_receipt.consumer_id == A04_CREATIVE_CONSUMER_ID
    assert reloaded_receipt.pulse_execution == "NOT_ATTEMPTED"
    assert pkt.a04_receipt_digest == reloaded_receipt.digest

    # Read-only PULSE CreativeDirective & PA2 state machine structural compatibility
    reloaded_directive = parse_contract("CreativeDirective", directive.raw)
    assert pkt.directive_bytes_sha256 == sha256(reloaded_directive.raw).hexdigest()
    assert pkt.authority.to_directive_authorization() == draft_authorization()

    journey = Journey(journey_id=pkt.journey_id, source_sha=pkt.source_sha)
    accepted = transition(
        journey,
        Event(
            event_id="event:compat-input",
            kind=EventKind.INPUT_ACCEPTED,
            journey_id=pkt.journey_id,
            source_sha=pkt.source_sha,
            record=reloaded_directive,
        ),
        at=1700000000,
    )
    assert accepted.state == State.INPUT_VALIDATED
    assert accepted.state != State.WAIT_BRAIN


def test_fail_closed_on_tampered_hashes_and_actual_bytes():
    receipt, receipt_raw, pkg_raw, res_raw = _make_receipt()
    directive, asset_map, rights_map = _make_directive()
    pkt = build_a04_pulse_handoff_packet(
        a04_receipt=receipt,
        directive=directive,
        asset_bytes_by_ref=asset_map,
        rights_bytes_by_ref=rights_map,
    )

    # Tampered packet digest
    tampered = pkt.to_dict()
    tampered["packet_sha256"] = _hex("0")
    with pytest.raises(A04PulseHandoffError, match="PACKET_DIGEST_MISMATCH"):
        validate_a04_pulse_handoff_packet(tampered, a04_receipt=receipt, directive=directive)

    # Tampered packet_id
    bad_id = pkt.to_dict()
    bad_id["packet_id"] = "a04pulse-" + _hex("0")
    with pytest.raises(A04PulseHandoffError, match="PACKET_ID_MISMATCH"):
        validate_a04_pulse_handoff_packet(bad_id, a04_receipt=receipt, directive=directive)

    # Non-canonical packet bytes
    pretty = json.dumps(pkt.to_dict(), indent=2).encode("utf-8")
    with pytest.raises(A04PulseHandoffError, match="PACKET_BYTES_NONCANONICAL"):
        parse_a04_pulse_handoff_packet(pretty)

    # Duplicate JSON key in packet bytes
    dup_raw = b'{"schema_version":"A04PulseOfflineHandoffPacket.v1","schema_version":"A04PulseOfflineHandoffPacket.v1"}'
    with pytest.raises(A04PulseHandoffError, match="DUPLICATE_JSON_KEY"):
        parse_a04_pulse_handoff_packet(dup_raw)

    # Mismatched directive_bytes
    with pytest.raises(A04PulseHandoffError, match="DIRECTIVE_BYTES_MISMATCH"):
        validate_a04_pulse_handoff_packet(
            pkt, a04_receipt=receipt, directive=directive, directive_bytes=directive.raw + b" "
        )

    # Mismatched receipt_bytes
    with pytest.raises(A04PulseHandoffError, match="RECEIPT_BYTES_MISMATCH"):
        validate_a04_pulse_handoff_packet(
            pkt, a04_receipt=receipt, directive=directive, receipt_bytes=receipt_raw + b" "
        )

    # Mismatched package_bytes
    with pytest.raises(A04PulseHandoffError, match="PACKAGE_BYTES_MISMATCH"):
        validate_a04_pulse_handoff_packet(
            pkt, a04_receipt=receipt, directive=directive, package_bytes=pkg_raw + b"x"
        )

    # Mismatched a03_result_bytes
    with pytest.raises(A04PulseHandoffError, match="A03_RESULT_BYTES_MISMATCH"):
        validate_a04_pulse_handoff_packet(
            pkt, a04_receipt=receipt, directive=directive, a03_result_bytes=res_raw + b"x"
        )

    # Mismatched asset_bytes
    asset_ref = next(iter(asset_map))
    with pytest.raises(A04PulseHandoffError, match="ASSET_BYTES_MISMATCH"):
        validate_a04_pulse_handoff_packet(
            pkt, a04_receipt=receipt, directive=directive, asset_bytes_by_ref={asset_ref: b"tampered"}
        )

    # Missing/extra asset ref in asset_bytes_by_ref
    with pytest.raises(A04PulseHandoffError, match="ASSET_BYTES_SET_MISMATCH"):
        validate_a04_pulse_handoff_packet(
            pkt,
            a04_receipt=receipt,
            directive=directive,
            asset_bytes_by_ref={**asset_map, "asset:" + _hex("9"): b"extra"},
        )

    # Mismatched rights_bytes
    rights_ref = next(iter(rights_map))
    with pytest.raises(A04PulseHandoffError, match="RIGHTS_BYTES_MISMATCH"):
        validate_a04_pulse_handoff_packet(
            pkt, a04_receipt=receipt, directive=directive, rights_bytes_by_ref={rights_ref: b"tampered"}
        )


def test_fail_closed_on_mismatched_stale_and_forged_versions_and_wrong_journey():
    receipt, _, _, _ = _make_receipt(revision=1)
    directive, _, _ = _make_directive(directive_version=2)
    pkt = build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=directive)

    # Forged packet schema_version
    bad_schema = pkt.to_dict()
    bad_schema["schema_version"] = "A04PulseOfflineHandoffPacket.v2"
    with pytest.raises(A04PulseHandoffError, match="INVALID_SCHEMA_VERSION"):
        A04PulseHandoffPacket.from_dict(bad_schema)

    # Stale directive_version
    with pytest.raises(A04PulseHandoffError, match="STALE_DIRECTIVE_VERSION"):
        validate_a04_pulse_handoff_packet(
            pkt, a04_receipt=receipt, directive=directive, min_directive_version=3
        )

    # Mismatched directive_version vs a newer directive
    newer_directive, _, _ = _make_directive(directive_version=3)
    with pytest.raises(A04PulseHandoffError, match="DIRECTIVE_VERSION_MISMATCH"):
        validate_a04_pulse_handoff_packet(pkt, a04_receipt=receipt, directive=newer_directive)

    # Stale package_revision
    with pytest.raises(A04PulseHandoffError, match="STALE_PACKAGE_REVISION"):
        validate_a04_pulse_handoff_packet(
            pkt, a04_receipt=receipt, directive=directive, min_package_revision=2
        )

    # Stale/mismatched source_sha
    with pytest.raises(A04PulseHandoffError, match="STALE_OR_MISMATCHED_SOURCE_SHA"):
        validate_a04_pulse_handoff_packet(
            pkt, a04_receipt=receipt, directive=directive, expected_source_sha="0" * 40
        )

    # Cross-input source_sha mismatch
    other_sha_receipt, _, _, _ = _make_receipt(source_sha="a" * 40)
    with pytest.raises(A04PulseHandoffError, match="SOURCE_SHA_MISMATCH"):
        build_a04_pulse_handoff_packet(a04_receipt=other_sha_receipt, directive=directive)

    # Wrong journey between receipt and directive
    other_journey_dir, _, _ = _make_directive(journey_id="journey:" + "99" * 16)
    with pytest.raises(A04PulseHandoffError, match="JOURNEY_MISMATCH"):
        build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=other_journey_dir)

    # Wrong expected_journey_id and expected_cycle_id
    with pytest.raises(A04PulseHandoffError, match="JOURNEY_MISMATCH"):
        validate_a04_pulse_handoff_packet(
            pkt, a04_receipt=receipt, directive=directive, expected_journey_id="journey:" + "88" * 16
        )
    with pytest.raises(A04PulseHandoffError, match="CYCLE_MISMATCH"):
        validate_a04_pulse_handoff_packet(
            pkt, a04_receipt=receipt, directive=directive, expected_cycle_id="cycle-wrong"
        )

    # Forged self-parent package lineage on revision 2
    self_pkg = b'{"self":"loop"}'
    loop_receipt, _, _, _ = _make_receipt(
        revision=2,
        parent_package_digest=sha256_bytes(self_pkg),
        package_bytes=self_pkg,
    )
    with pytest.raises(A04PulseHandoffError, match="FORGED_PACKAGE_LINEAGE"):
        build_a04_pulse_handoff_packet(a04_receipt=loop_receipt, directive=directive)


def test_fail_closed_on_absent_rights_scope_and_authority_violations():
    receipt, _, _, _ = _make_receipt()

    # Absent rights (UNKNOWN / RESTRICTED)
    for bad_status in ("UNKNOWN", "RESTRICTED"):
        bad_dir, _, _ = _make_directive(rights_status=bad_status)
        with pytest.raises(A04PulseHandoffError, match="RIGHTS_ABSENT_OR_RESTRICTED"):
            build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=bad_dir)

    # Fixture rights on human-supplied provenance
    mixed_dir, _, _ = _make_directive(fixture_mode=False, asset_evidence_type="SYNTHETIC_FIXTURE", rights_status="NOT_APPLICABLE")
    with pytest.raises(A04PulseHandoffError, match="FIXTURE_RIGHTS_MISMATCH"):
        build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=mixed_dir)

    # Unproven asset evidence type
    unknown_ev_dir, _, _ = _make_directive(asset_evidence_type="UNKNOWN")
    with pytest.raises(A04PulseHandoffError, match="UNPROVEN_ASSET_EVIDENCE"):
        build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=unknown_ev_dir)

    # Empty assets
    empty_assets_dir, _, _ = _make_directive(assets_override=[])
    with pytest.raises(A04PulseHandoffError, match="ASSETS_REQUIRED"):
        build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=empty_assets_dir)

    # Absent allowed_tools
    no_tools_dir, _, _ = _make_directive(allowed_tools=[])
    with pytest.raises(A04PulseHandoffError, match="OFFLINE_SCOPE_REQUIRED"):
        build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=no_tools_dir)

    # Non-offline action_class (PUBLISH / GENERATE_MEDIA)
    for bad_action in ("PUBLISH", "GENERATE_MEDIA"):
        bad_action_dir, _, _ = _make_directive(action_class=bad_action, action_classes=[bad_action])
        with pytest.raises(A04PulseHandoffError, match="OFFLINE_SCOPE_REQUIRED"):
            build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=bad_action_dir)

    # Non-null provider_ref in scope
    provider_scope_dir, _, _ = _make_directive(scope_overrides={"provider_ref": "provider:external"})
    with pytest.raises(A04PulseHandoffError, match="OFFLINE_SCOPE_REQUIRED"):
        build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=provider_scope_dir)

    # scope.asset_sha256 not matching any pinned asset
    unpinned_scope_dir, _, _ = _make_directive(scope_overrides={"asset_sha256": _hex("7")})
    with pytest.raises(A04PulseHandoffError, match="SCOPE_ASSET_MISMATCH"):
        build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=unpinned_scope_dir)

    # Forged authority claiming network / WAIT_BRAIN / PULSE execution / WakeHub
    good_dir, _, _ = _make_directive()
    pkt = build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=good_dir)
    for field_name, bad_val in (
        ("allow_network", True),
        ("allow_spend", True),
        ("allow_publish", True),
        ("provider_entitlement", True),
        ("wait_brain_claimed", True),
        ("pulse_receiver_execution", "EXECUTED"),
        ("wakehub_effect", "DISPATCHED"),
    ):
        forged = pkt.to_dict()
        forged["authority"][field_name] = bad_val
        with pytest.raises(A04PulseHandoffError, match="OFFLINE_AUTHORITY_VIOLATION"):
            A04PulseHandoffPacket.from_dict(forged)

    # Invalid CTA combinations
    with pytest.raises(A04PulseHandoffError, match="INVALID_CTA_PAYLOAD"):
        build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=good_dir, cta_mode="NONE", cta_text="Buy now")
    with pytest.raises(A04PulseHandoffError, match="INVALID_CTA_PAYLOAD"):
        build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=good_dir, cta_mode="EXPLICIT", cta_text=None)


def test_fail_closed_on_duplicated_logical_identity():
    receipt, _, _, _ = _make_receipt()
    directive, _, _ = _make_directive()

    # Duplicate asset_sha256 under two distinct asset refs inside one directive
    raw_a = b"identical-still-bytes"
    sha_a = sha256_bytes(raw_a)
    rights_sha = sha256_bytes(b"rights-a")
    dup_assets_dir, _, _ = _make_directive(
        assets_override=[
            {
                "ref": "asset:one",
                "sha256": sha_a,
                "evidence_type": "HUMAN_SUPPLIED",
                "rights": {"status": "GRANTED", "ref": "rights:one", "sha256": rights_sha},
            },
            {
                "ref": "asset:two",
                "sha256": sha_a,
                "evidence_type": "HUMAN_SUPPLIED",
                "rights": {"status": "GRANTED", "ref": "rights:one", "sha256": rights_sha},
            },
        ]
    )
    with pytest.raises(A04PulseHandoffError, match="DUPLICATE_ASSET_IDENTITY"):
        build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=dup_assets_dir)

    # Same logical identity across two packets with different CTA text
    pkt_none = build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=directive, cta_mode="NONE")
    pkt_cta = build_a04_pulse_handoff_packet(
        a04_receipt=receipt, directive=directive, cta_mode="EXPLICIT", cta_text="Shop"
    )
    assert pkt_none.packet_id != pkt_cta.packet_id
    assert pkt_none.logical_identity == pkt_cta.logical_identity
    assert pkt_none.logical_identity == derive_logical_identity(
        journey_id=receipt.journey_id,
        cycle_id=receipt.cycle_id,
        job_id=receipt.job_id,
        attempt=receipt.attempt,
        package_id=receipt.package_id,
        package_revision=receipt.revision,
        directive_version=1,
    )

    with pytest.raises(A04PulseHandoffError, match="DUPLICATE_LOGICAL_IDENTITY"):
        check_unique_logical_identities((pkt_none, pkt_cta))

    with pytest.raises(A04PulseHandoffError, match="DUPLICATE_LOGICAL_IDENTITY"):
        validate_a04_pulse_handoff_packet(
            pkt_cta,
            a04_receipt=receipt,
            directive=directive,
            seen_identities={pkt_none.logical_identity},
        )

    with pytest.raises(A04PulseHandoffError, match="DUPLICATE_LOGICAL_IDENTITY"):
        validate_a04_pulse_handoff_packet(
            pkt_cta,
            a04_receipt=receipt,
            directive=directive,
            seen_packets=(pkt_none,),
        )

    # Reusing the same A04 receipt with a bumped directive_version also fails closed
    dir_v2, _, _ = _make_directive(directive_version=2)
    pkt_v2 = build_a04_pulse_handoff_packet(a04_receipt=receipt, directive=dir_v2)
    with pytest.raises(A04PulseHandoffError, match="DUPLICATE_LOGICAL_IDENTITY"):
        check_unique_logical_identities((pkt_none, pkt_v2))


def test_source_boundary_has_no_network_subprocess_provider_or_wakehub_imports():
    repo = Path(__file__).resolve().parents[1]
    pkg_dir = repo / "src" / "ai_commerce" / "a04_pulse_handoff"
    forbidden = {
        "urllib", "http", "socket", "requests", "httpx", "subprocess",
        "os", "sys", "time", "datetime", "ctypes", "msvcrt", "winreg",
        "ai_commerce.wakehub", "ai_commerce.wakehub_runtime",
        "ai_commerce.wakehub_cdp", "ai_commerce.providers",
        "ai_commerce.production_orchestration",
    }
    py_files = sorted(pkg_dir.glob("*.py"))
    assert [p.name for p in py_files] == ["__init__.py", "handoff.py"]
    for path in py_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden
                    assert alias.name not in forbidden
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden
                assert node.module not in forbidden
    assert canonical_handoff_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
