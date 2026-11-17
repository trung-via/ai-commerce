"""Offline contract/owner evidence; no live dispatch or publication proof."""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import socket

import pytest

from ai_commerce.creative.briefs import brief_digest, intake_brief
from ai_commerce.opportunity.bridge.briefs import opportunity_to_brief
from ai_commerce.opportunity.proposals import propose_opportunities
from ai_commerce.production_orchestration import (
    CONSUMER_ROUTES, OrchestrationError, ProductionPipelineOrchestrator,
    ProductionStageCompleted, ProductionStageDispatched, ProductionStageReceipt,
    Provenance, RouteAuthorization, StageSpec, bytes_digest, canonical_bytes, digest,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "Synthetic Human direction: a quiet cloud.".encode("utf-8")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Network attempted")
    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def stage(route="a03-creative-brief", key="synthetic-stage-1"):
    if route == "a10-m05-content-brief":
        input_value = propose_opportunities(as_of="2026-10-29T12:00:00Z", manual_idea=SOURCE.decode())[0]
        output = opportunity_to_brief(input_value, synthetic=True)
    else:
        input_value = intake_brief(SOURCE.decode())
        output = intake_brief(input_value)
    consumer, path = CONSUMER_ROUTES[route]
    provenance = Provenance(actor_ref="fixture:producer", source_ref="fixture:human-direction",
                            source_sha256=bytes_digest(SOURCE), evidence_type="SYNTHETIC_FIXTURE")
    spec = StageSpec(stage_id="stage:brief", route_id=route, consumer_id=consumer, consumer_path=path,
                     source_sha256=bytes_digest(SOURCE), input_sha256=digest(input_value),
                     output_sha256=digest(output), idempotency_key=key, provenance=provenance,
                     authorization=RouteAuthorization(route_id=route, consumer_id=consumer,
                                                      consumer_path=path, authorization_ref="fixture:route-permit"))
    completed = ProductionStageCompleted(spec=spec, completion_ref="fixture:completed:" + key,
                                         source_sha256=spec.source_sha256, input_sha256=spec.input_sha256,
                                         output_sha256=spec.output_sha256)
    return completed, input_value, output


def owner(completed, receipt=None):
    spec = completed.spec
    return ProductionPipelineOrchestrator(
        authorized_specs=((spec.authorization.authorization_ref, spec.sha256),),
        verified_receipts=() if receipt is None else ((receipt.consumer_id, receipt.sha256),))


def record(orchestrator, completed, input_value, output):
    return orchestrator.record_completed(completed, source_bytes=SOURCE, input_value=input_value, output=output)


def receipt_after_offline_execution(completed, input_value):
    spec = completed.spec
    if spec.route_id == "a10-m05-content-brief":
        actual = opportunity_to_brief(input_value, synthetic=True)
    else:
        actual = intake_brief(input_value)
    # The fixture actually calls the selected local consumer before authoring
    # its synthetic receipt. This is not provider or production attestation.
    assert digest(actual) == spec.output_sha256
    return ProductionStageReceipt(spec=spec, completed_sha256=completed.sha256,
                                  output_sha256=digest(actual), consumer_id=spec.consumer_id,
                                  receipt_ref="fixture:receipt:" + spec.idempotency_key,
                                  execution_ref="fixture:execution:" + spec.idempotency_key,
                                  provenance=replace(spec.provenance, actor_ref=spec.consumer_id),
                                  consumer_executed=True)


@pytest.mark.parametrize("route", tuple(CONSUMER_ROUTES))
def test_real_consumer_snapshots_need_separate_receipts(route):
    completed, input_value, output = stage(route)
    receipt = receipt_after_offline_execution(completed, input_value)
    orchestrator = owner(completed, receipt)
    result = record(orchestrator, completed, input_value, output)
    assert result.status == "NOT_DISPATCHED"
    assert result.accepted_consumer is None and result.event is None
    assert digest(output) == brief_digest(output)
    result = orchestrator.record_receipt(receipt)
    assert result.status == "RECEIPT_RECORDED"
    assert result.accepted_consumer == completed.spec.consumer_id
    assert result.event.receipt == receipt
    assert result.event.receipt_sha256 == receipt.sha256


def test_dispatch_has_no_consumer_execution(monkeypatch):
    completed, input_value, output = stage()
    orchestrator = owner(completed)
    record(orchestrator, completed, input_value, output)
    def deny(*args, **kwargs):
        raise AssertionError("Consumer execution attempted")
    monkeypatch.setattr("ai_commerce.creative.briefs.intake_brief", deny)
    monkeypatch.setattr("ai_commerce.opportunity.bridge.briefs.opportunity_to_brief", deny)
    for _ in range(2):
        result = orchestrator.dispatch(completed.spec.idempotency_key)
        assert result.status == "NOT_DISPATCHED" and result.accepted_consumer is None


def test_strict_wire_roundtrip_versions_missing_and_extra_fields():
    completed, input_value, _ = stage()
    receipt = receipt_after_offline_execution(completed, input_value)
    dispatched = ProductionStageDispatched(spec=completed.spec, completed_sha256=completed.sha256,
                                           receipt=receipt, receipt_sha256=receipt.sha256)
    for value in (completed.spec, completed, receipt, dispatched):
        kind = type(value)
        wire = json.loads(canonical_bytes(value.to_dict()))
        assert kind.from_dict(wire) == value
        for version in (None, 1, "v1", value.schema_version.replace(".v1", ".v2")):
            with pytest.raises(OrchestrationError, match="SCHEMA_VERSION"):
                kind.from_dict({**wire, "schema_version": version})
        with pytest.raises(OrchestrationError, match="CONTRACT_FIELDS"):
            kind.from_dict({**wire, "unreviewed": True})
        del wire["schema_version"]
        with pytest.raises(OrchestrationError, match="CONTRACT_FIELDS"):
            kind.from_dict(wire)
    with pytest.raises(FrozenInstanceError):
        completed.spec.stage_id = "modified"


@pytest.mark.parametrize("field", ("source_sha256", "input_sha256", "output_sha256"))
@pytest.mark.parametrize("bad", (None, 7, "", "a" * 63, "g" * 64, "A" * 64, "sha256:" + "a" * 64))
def test_hash_shapes_are_strict(field, bad):
    completed, _, _ = stage()
    with pytest.raises(OrchestrationError, match="INVALID_SHA256"):
        replace(completed.spec, **{field: bad})
    with pytest.raises(OrchestrationError, match="INVALID_SHA256"):
        replace(completed, **{field: bad})


@pytest.mark.parametrize("part", ("source", "input", "output"))
def test_valid_hash_syntax_does_not_hide_changed_content(part):
    completed, input_value, output = stage()
    args = dict(source_bytes=SOURCE, input_value=input_value, output=output)
    if part == "source":
        args["source_bytes"] += b" changed"
    else:
        name = "input_value" if part == "input" else "output"
        args[name] = {**args[name], "objective": "different"}
    with pytest.raises(OrchestrationError, match=part.upper() + "_HASH_MISMATCH"):
        owner(completed).record_completed(completed, **args)


def test_source_provenance_completed_and_output_pins_must_agree():
    completed, _, _ = stage()
    with pytest.raises(OrchestrationError, match="PROVENANCE_SOURCE_MISMATCH"):
        replace(completed.spec, source_sha256="0" * 64)
    with pytest.raises(OrchestrationError, match="COMPLETED_HASH_MISMATCH"):
        replace(completed, output_sha256="0" * 64)
    with pytest.raises(OrchestrationError, match="OUTPUT_CONTRACT"):
        replace(completed.spec, output_contract="ContentBrief.v2")


def test_route_consumer_and_independent_authorization_are_bound():
    completed, input_value, output = stage()
    spec = completed.spec
    for change in ({"route_id": "unregistered"}, {"consumer_id": "unregistered:consumer"},
                   {"consumer_path": "src/ai_commerce/missing.py"}):
        with pytest.raises(OrchestrationError, match="CONSUMER_BINDING"):
            replace(spec, **change)
    other = stage("a10-m05-content-brief")[0].spec.authorization
    with pytest.raises(OrchestrationError, match="ROUTE_AUTHORIZATION_MISMATCH"):
        replace(spec, authorization=other)
    with pytest.raises(OrchestrationError, match="AUTHORIZATION_SCOPE"):
        replace(spec.authorization, scope="EXECUTE")
    with pytest.raises(OrchestrationError, match="ROUTE_NOT_AUTHORIZED"):
        record(ProductionPipelineOrchestrator(), completed, input_value, output)
    changed = replace(completed, spec=replace(spec, idempotency_key="different"))
    with pytest.raises(OrchestrationError, match="ROUTE_NOT_AUTHORIZED"):
        record(owner(completed), changed, input_value, output)


def test_idempotent_replay_and_conflicting_completion():
    completed, input_value, output = stage()
    receipt = receipt_after_offline_execution(completed, input_value)
    orchestrator = owner(completed, receipt)
    assert record(orchestrator, completed, input_value, output) == record(orchestrator, completed, input_value, output)
    conflict = replace(completed, completion_ref="fixture:another-completion")
    with pytest.raises(OrchestrationError, match="IDEMPOTENCY_CONFLICT"):
        record(orchestrator, conflict, input_value, output)
    first = orchestrator.record_receipt(receipt)
    assert orchestrator.record_receipt(receipt) == first
    with pytest.raises(OrchestrationError, match="IDEMPOTENCY_CONFLICT"):
        orchestrator.record_receipt(replace(receipt, execution_ref="fixture:another-execution"))
    assert orchestrator.dispatch(completed.spec.idempotency_key) == first


def test_missing_malformed_and_mismatched_receipts_never_accept_consumer():
    completed, input_value, output = stage()
    orchestrator = owner(completed)
    record(orchestrator, completed, input_value, output)
    for bad in (None, {}, {"consumer_executed": True}, output):
        with pytest.raises(OrchestrationError, match="RECEIPT_REQUIRED"):
            orchestrator.record_receipt(bad)
    receipt = receipt_after_offline_execution(completed, input_value)
    for change, code in (({"consumer_executed": False}, "CONSUMER_NOT_EXECUTED"),
                         ({"consumer_executed": 1}, "CONSUMER_NOT_EXECUTED"),
                         ({"execution_ref": ""}, "INVALID_REFERENCE"),
                         ({"consumer_id": "unregistered:consumer"}, "RECEIPT_CONSUMER_MISMATCH"),
                         ({"output_sha256": "0" * 64}, "RECEIPT_OUTPUT_MISMATCH")):
        with pytest.raises(OrchestrationError, match=code):
            replace(receipt, **change)
    with pytest.raises(OrchestrationError, match="RECEIPT_COMPLETION_MISMATCH"):
        orchestrator.record_receipt(replace(receipt, completed_sha256="0" * 64))
    with pytest.raises(OrchestrationError, match="COMPLETED_NOT_FOUND"):
        owner(completed).record_receipt(receipt)
    assert orchestrator.dispatch(completed.spec.idempotency_key).accepted_consumer is None


def test_receipt_and_execution_references_cannot_be_reused_for_another_stage():
    one, input_value, output = stage(key="first")
    two = replace(one, spec=replace(one.spec, idempotency_key="second"), completion_ref="fixture:second")
    first = receipt_after_offline_execution(one, input_value)
    second = receipt_after_offline_execution(two, input_value)
    orchestrator = ProductionPipelineOrchestrator(authorized_specs=tuple(
        (c.spec.authorization.authorization_ref, c.spec.sha256) for c in (one, two)),
        verified_receipts=tuple((r.consumer_id, r.sha256) for r in (first, second)))
    for completed in (one, two):
        record(orchestrator, completed, input_value, output)
    orchestrator.record_receipt(first)
    for change in ({"receipt_ref": first.receipt_ref}, {"execution_ref": first.execution_ref}):
        with pytest.raises(OrchestrationError, match="RECEIPT_REPLAY"):
            orchestrator.record_receipt(replace(second, **change))
    assert orchestrator.dispatch("second").status == "NOT_DISPATCHED"


def test_wire_receipt_cannot_attest_its_own_execution():
    completed, input_value, output = stage()
    receipt = receipt_after_offline_execution(completed, input_value)
    for pins in ((), ((receipt.consumer_id, "0" * 64),), (("wrong:consumer", receipt.sha256),)):
        orchestrator = ProductionPipelineOrchestrator(
            authorized_specs=((completed.spec.authorization.authorization_ref, completed.spec.sha256),),
            verified_receipts=pins)
        record(orchestrator, completed, input_value, output)
        with pytest.raises(OrchestrationError, match="RECEIPT_NOT_VERIFIED"):
            orchestrator.record_receipt(receipt)
        assert orchestrator.dispatch(completed.spec.idempotency_key).accepted_consumer is None


@pytest.mark.parametrize("route", tuple(CONSUMER_ROUTES))
def test_repinned_output_still_requires_the_selected_consumer_contract(route):
    completed, input_value, output = stage(route)
    if route == "a10-m05-content-brief":
        output = {k: v for k, v in output.items() if k != "opportunity_bridge"}
        code = "CONSUMER_CONTRACT_INVALID"
    else:
        output = {**output, "objective": "A different valid brief"}
        code = "A03_INPUT_BINDING"
    spec = replace(completed.spec, output_sha256=digest(output))
    changed = replace(completed, spec=spec, output_sha256=spec.output_sha256)
    with pytest.raises(OrchestrationError, match=code):
        record(owner(changed), changed, input_value, output)


def test_missing_receipt_fields_wrong_route_and_provenance():
    completed, input_value, output = stage()
    receipt = receipt_after_offline_execution(completed, input_value)
    for field in ("receipt_ref", "execution_ref", "consumer_executed", "provenance"):
        wire = receipt.to_dict()
        del wire[field]
        with pytest.raises(OrchestrationError, match="CONTRACT_FIELDS"):
            ProductionStageReceipt.from_dict(wire)
    with pytest.raises(OrchestrationError, match="RECEIPT_PROVENANCE_MISMATCH"):
        replace(receipt, provenance=replace(receipt.provenance, source_sha256="0" * 64))
    orchestrator = owner(completed)
    record(orchestrator, completed, input_value, output)
    other, other_input, _ = stage("a10-m05-content-brief")
    with pytest.raises(OrchestrationError, match="RECEIPT_COMPLETION_MISMATCH"):
        orchestrator.record_receipt(receipt_after_offline_execution(other, other_input))
    with pytest.raises(OrchestrationError, match="RECEIPT_REQUIRED"):
        ProductionStageDispatched(spec=completed.spec, completed_sha256=completed.sha256,
                                  receipt=None, receipt_sha256="0" * 64)
    with pytest.raises(OrchestrationError, match="DISPATCH_RECEIPT_HASH_MISMATCH"):
        ProductionStageDispatched(spec=completed.spec, completed_sha256=completed.sha256,
                                  receipt=receipt, receipt_sha256="0" * 64)
    assert orchestrator.dispatch(completed.spec.idempotency_key).status == "NOT_DISPATCHED"


def test_manifest_uses_real_registered_source_owners_and_test_closure():
    from ai_commerce.engineering.impact_v21 import select_impact, validate_component_onboarding_manifest
    from ai_commerce.engineering.minimum_test import TestGroup, approved_groups
    manifest_path = "work_items/mt01-components/production-orchestration.json"
    manifest = json.loads((ROOT / manifest_path).read_bytes())
    assert manifest["sources"] == [f"src/ai_commerce/production_orchestration/{name}.py"
                                   for name in ("__init__", "contracts", "orchestrator")]
    assert manifest["tests"] == ["tests/test_production_orchestration.py"]
    assert manifest["acs"] == ["PRODUCTION.orchestration-p0"]
    assert set(manifest["consumers"]) == {path for _, path in CONSUMER_ROUTES.values()}
    groups = approved_groups()
    owners = {g.name: g for g in groups if set(g.paths) & set(manifest["consumers"])}
    assert set(owners) == {"a03", "a10"}
    assert "tests/test_a03_creative.py" in owners["a03"].tests
    assert "tests/test_a10_m05.py" in owners["a10"].tests
    for path in (*manifest["sources"], *manifest["tests"], *manifest["consumers"],
                 *(test for group in owners.values() for test in group.tests)):
        assert (ROOT / path).is_file()
    changed = (*manifest["sources"], *manifest["tests"], manifest_path)
    # Static admission/selection only. EF01 independently binds the immutable
    # base tree and proves actual collection/execution; this test does neither.
    admission = validate_component_onboarding_manifest(
        manifest, manifest_path=manifest_path, changed_paths=changed,
        base_paths=tuple(p for g in groups for p in (*g.paths, *g.tests, *g.consumers, *g.contracts)),
        existing_groups=groups, allowed_paths=changed, root=ROOT)
    assert admission["status"] == "VALIDATED_DATA_ONLY_NOT_VERIFICATION_PASS"
    new_owner = TestGroup(name=admission["name"], tests=admission["tests"], acs=admission["acs"],
                          paths=admission["paths"], consumers=admission["consumers"],
                          contracts=(manifest_path,))
    plan = select_impact(root=ROOT, changed_paths=changed, groups=(*groups, new_owner))
    assert plan.runnable, plan.missing_mapping
    assert set(plan.selected_groups) == {"production-orchestration", "a03", "a10"}
    assert set(plan.selected_tests) == {
        *manifest["tests"], *(test for group in owners.values() for test in group.tests),
    }
    assert "ONBOARD_CONSUMER_OWNER_CLOSURE" in plan.reason_codes
