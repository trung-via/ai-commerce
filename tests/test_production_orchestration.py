"""Offline contract/owner evidence; no live dispatch or publication proof."""
import copy
from dataclasses import FrozenInstanceError, replace
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess

import pytest

from ai_commerce.creative.briefs import brief_digest, intake_brief
from ai_commerce.opportunity.bridge.briefs import opportunity_to_brief
from ai_commerce.opportunity.proposals import propose_opportunities
from ai_commerce.production_orchestration import (
    CONSUMER_ROUTES, LocalAuthorization, OrchestrationError, ProductionPipelineOrchestrator,
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


P0_BRIEF_ROUTES = ("a10-m05-content-brief", "a03-creative-brief")


@pytest.mark.parametrize("route", P0_BRIEF_ROUTES)
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


@pytest.mark.parametrize("route", P0_BRIEF_ROUTES)
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
    route_paths = {path for _, path in CONSUMER_ROUTES.values()}
    assert set(manifest["consumers"]) <= route_paths
    groups = approved_groups()
    owners = {g.name: g for g in groups if set(g.paths) & set(manifest["consumers"])}
    assert set(owners) == {"a03", "a10"}
    route_owners = {g.name: g for g in groups if set(g.paths) & route_paths}
    assert set(route_owners) == {"a03", "a04", "a10"}
    assert "tests/test_a03_creative.py" in owners["a03"].tests
    assert "tests/test_a10_m05.py" in owners["a10"].tests
    assert "tests/test_a04_upgrade_v11.py" in route_owners["a04"].tests
    for path in (*manifest["sources"], *manifest["tests"], *manifest["consumers"], *route_paths,
                 *(test for group in route_owners.values() for test in group.tests)):
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
    # MT-01 selects only the owner, its immediate/two-hop consumers and the
    # explicitly registered onboarding owners. Deeper transitive imports are
    # reported for review; they must not balloon the mandatory CI test set.
    expected_groups = {
        "production-orchestration", "a03", "a04", "a10", "cli",
    }
    assert set(plan.selected_groups) == expected_groups
    assert any(w.startswith("DEFERRED_TRANSITIVE_IMPORT_PATHS:") for w in plan.inventory_warnings)
    assert set(plan.selected_tests) == {
        test for group in (*groups, new_owner) if group.name in expected_groups
        for test in group.tests
    }
    assert "ONBOARD_CONSUMER_OWNER_CLOSURE" in plan.reason_codes


@pytest.fixture
def local_case(tmp_path):
    """Real synthetic M01-M05 producer; no model or alternate domain store."""
    from ai_commerce.opportunity import run_automation
    module = importlib.util.spec_from_file_location("production_synthetic_demo", ROOT / "scripts/a10_demo.py")
    demo = importlib.util.module_from_spec(module)
    module.loader.exec_module(demo)
    raw = canonical_bytes({"schema_version": "ManualSignalRows.v1", "rows": [
        demo._row(demo.JSON_TOPIC, day, count) for day, count in ((27, 0), (28, 0), (29, 4))]})
    (tmp_path / "signals.json").write_bytes(raw)
    manifest = {"schema_version": "A10AutomationJob.v1", "job_id": "synthetic-production",
        "source_sha": "b" * 40, "topic": demo.JSON_TOPIC, "as_of": demo.AS_OF,
        "provider": None, "ai_assessment": False, "sources": [{"file": "signals.json", "format": "json",
        "sha256": bytes_digest(raw), "snapshot": demo._snapshot(raw, "production"),
        "comparison_context": demo._comparison()}], "allowed_effects": ["IMPORT_SIGNALS", "CREATE_BRIEF"]}
    result = run_automation(manifest, base_dir=tmp_path, source_sha=manifest["source_sha"],
        state_dir=tmp_path / "a10", mode="offline", permissions=manifest["allowed_effects"])
    assert result["status"] == "COMPLETE" and result["reason"] == "A03_BRIEF_CREATED"
    grant = LocalAuthorization(journey_id="synthetic:journey", cycle_id="cycle:1", job_id=manifest["job_id"],
        source_sha=manifest["source_sha"], authorization_ref="fixture:local-grant")
    def make_owner(**changes):
        return ProductionPipelineOrchestrator(**{ "state_dir": tmp_path / "production",
            "source_sha": grant.source_sha, "local_authorization": grant, **changes})
    refs = {"result_ref": tmp_path / "a10" / manifest["job_id"] / (result["event_id"] + ".json"),
            "brief_ref": Path(result["brief_ref"])}
    return result, refs, make_owner


def invocation_rows(owner, kind):
    with sqlite3.connect(owner._state_dir / "production.sqlite") as db:
        return [json.loads(row[0]) for row in db.execute("SELECT body FROM journal WHERE kind=?", (kind,))]


def test_local_p1_true_receiver_receipt_and_durable_duplicate(local_case, monkeypatch):
    result, refs, make_owner = local_case
    from ai_commerce.creative import briefs
    real, calls = briefs.intake_brief, []
    def observe(raw):
        # CreativeStore also revalidates mappings; count the exact text handoff.
        if isinstance(raw, str):
            calls.append(raw.encode("utf-8"))
        return real(raw)
    monkeypatch.setattr(briefs, "intake_brief", observe)
    owner = make_owner()
    actual = owner.complete_a10(result, **refs)
    assert actual.status == "SUCCESS" and actual.event.receipt.outcome == "SUCCESS"
    assert calls == [refs["brief_ref"].read_bytes()]
    assert actual.event.receipt.execution_ref.startswith("local:a03:")
    assert actual.event.receipt.output_sha256 == result["brief_sha256"]
    assert actual.completed.spec.local_handoff.source_sha == result["source_sha"]
    from ai_commerce.creative import CreativeStore
    assert CreativeStore(owner._state_dir / "creative").load_brief(result["brief_sha256"]) == result["brief"]
    assert all(result["brief"][k] is None for k in ("audience", "product_ref", "cta"))
    reopened = make_owner()
    assert reopened.complete_a10(result, **refs) == actual
    assert reopened.resume_local() == (actual,)
    assert len(calls) == 1
    for kind in ("ProductionStageCompleted.v1", "ProductionDispatchAttempt.v1", "A03LocalInvocation.v1",
                 "ProductionStageReceipt.v1", "ProductionStageDispatched.v1"):
        assert len(invocation_rows(owner, kind)) == 1
    request = ProductionStageDispatched.from_dict(invocation_rows(owner, "ProductionStageDispatched.v1")[0])
    assert request.phase == "DISPATCHED" and request.receipt is None and request.receipt_sha256 is None
    assert request.completed_sha256 == actual.completed.sha256
    with sqlite3.connect(owner._state_dir / "production.sqlite") as db:
        kinds = [row[0] for row in db.execute("SELECT kind FROM journal ORDER BY seq")]
    assert kinds.index("ProductionStageCompleted.v1") < kinds.index("ProductionStageDispatched.v1")
    assert kinds.index("ProductionStageDispatched.v1") < kinds.index("A03LocalInvocation.v1")
    assert kinds.index("A03LocalInvocation.v1") < kinds.index("ProductionStageReceipt.v1")
    # Wire receipts cannot insert/attest a durable P1 invocation.
    with pytest.raises(OrchestrationError, match="COMPLETED_NOT_FOUND"):
        reopened.record_receipt(actual.event.receipt)


def test_local_outbox_commit_survives_crash_before_dispatch(local_case, monkeypatch):
    result, refs, make_owner = local_case
    first = make_owner()
    pending = first.complete_a10(result, **refs, dispatch=False)
    assert pending.status == "PENDING" and pending.event is None
    assert invocation_rows(first, "ProductionStageCompleted.v1") == [pending.completed.to_dict()]
    assert not invocation_rows(first, "A03LocalInvocation.v1")
    assert not invocation_rows(first, "ProductionStageDispatched.v1")
    resumed = make_owner().resume_local()
    assert len(resumed) == 1 and resumed[0].status == "SUCCESS"


class LocalCrash(BaseException):
    pass


@pytest.mark.parametrize("window", ["after-intake", "after-witness"])
def test_local_crash_windows_never_blindly_reinvoke(local_case, monkeypatch, window):
    result, refs, make_owner = local_case
    from ai_commerce.creative import briefs
    first, calls = make_owner(), []
    real_intake = briefs.intake_brief
    def observe(raw):
        if isinstance(raw, str):
            calls.append(raw)
        return real_intake(raw)
    monkeypatch.setattr(briefs, "intake_brief", observe)
    real = first._invoke_a03
    def invoke(completed, raw):
        witness = real(completed, raw)
        if window == "after-intake":
            raise LocalCrash()
        return witness
    monkeypatch.setattr(first, "_invoke_a03", invoke)
    if window == "after-witness":
        def crash(*args):
            raise LocalCrash()
        monkeypatch.setattr(first, "_finish_local", crash)
    with pytest.raises(LocalCrash):
        first.complete_a10(result, **refs)
    assert not invocation_rows(first, "ProductionStageReceipt.v1")
    monkeypatch.setattr("ai_commerce.production_orchestration.orchestrator.time.time", lambda: 10**12)
    second = make_owner()
    recovered = second.resume_local()[0]
    assert recovered.status == ("UNKNOWN" if window == "after-intake" else "SUCCESS")
    assert second.resume_local() == (recovered,)
    assert len(calls) == 1
    assert len(invocation_rows(first, "A03LocalInvocation.v1")) == (window == "after-witness")
    assert len(invocation_rows(first, "ProductionStageReceipt.v1")) == (window == "after-witness")


def test_local_active_lease_and_reordered_delivery(local_case, monkeypatch):
    result, refs, make_owner = local_case
    first, second = make_owner(), make_owner()
    pending = first.complete_a10(result, **refs, dispatch=False)
    key = pending.completed.spec.idempotency_key
    with pytest.raises(OrchestrationError, match="COMPLETED_NOT_FOUND"):
        second.dispatch_local("reordered:missing-completion")
    real = first._invoke_a03
    observations = []
    def invoke(completed, raw):
        observations.append(second.dispatch_local(key))
        return real(completed, raw)
    monkeypatch.setattr(first, "_invoke_a03", invoke)
    actual = first.dispatch_local(key)
    assert observations[0].status == "RUNNING" and observations[0].event is None
    assert actual.status == "SUCCESS" and second.dispatch_local(key) == actual
    assert len(invocation_rows(first, "ProductionDispatchAttempt.v1")) == 1


@pytest.mark.parametrize("failure,expected", [("reject", "UNKNOWN"), ("alter", "UNKNOWN"), ("unknown", "UNKNOWN")])
def test_local_receiver_failure_never_mints_success(local_case, monkeypatch, failure, expected):
    result, refs, make_owner = local_case
    from ai_commerce.creative import briefs
    real, calls = briefs.intake_brief, []
    def fail(raw):
        calls.append(raw)
        if failure == "reject":
            raise ValueError("synthetic rejection")
        if failure == "unknown":
            raise RuntimeError("synthetic unknown receiver failure")
        return {**real(raw), "objective": "altered receiver output"}
    monkeypatch.setattr(briefs, "intake_brief", fail)
    first = make_owner()
    actual = first.complete_a10(result, **refs)
    assert actual.status == expected and actual.event is None and actual.accepted_consumer is None
    assert actual.reason == "RECEIVER_UNRESOLVED"
    assert make_owner().resume_local() == (actual,)
    assert len(calls) == 1
    assert not invocation_rows(first, "ProductionStageReceipt.v1")


@pytest.mark.parametrize("target", ["brief_ref", "result_ref"])
def test_local_altered_immutable_bytes_fail_closed(local_case, monkeypatch, target):
    result, refs, make_owner = local_case
    first = make_owner()
    first.complete_a10(result, **refs, dispatch=False)
    refs[target].write_bytes(refs[target].read_bytes() + b" ")
    actual = make_owner().resume_local()[0]
    assert actual.status == "FAILED" and actual.event is None
    assert not invocation_rows(first, "ProductionDispatchAttempt.v1")


def test_local_malformed_brief_cannot_resume_to_success(local_case):
    result, refs, make_owner = local_case
    owner = make_owner()
    owner.complete_a10(result, **refs, dispatch=False)
    refs["brief_ref"].write_bytes(b"{\n")
    rejected, = make_owner().resume_local()
    assert (rejected.status, rejected.reason, rejected.event) == ("FAILED", "INPUT_REJECTED", None)
    assert not invocation_rows(owner, "ProductionDispatchAttempt.v1")
    assert not invocation_rows(owner, "ProductionStageReceipt.v1")


def test_local_same_completion_with_conflicting_grant_preserves_original_slot(local_case):
    result, refs, make_owner = local_case
    owner = make_owner()
    pending = owner.complete_a10(result, **refs, dispatch=False)
    changed_grant = replace(owner._local_authorization, authorization_ref="fixture:conflicting-grant")
    with pytest.raises(OrchestrationError, match="IDEMPOTENCY_CONFLICT"):
        make_owner(local_authorization=changed_grant).complete_a10(result, **refs)
    assert invocation_rows(owner, "ProductionStageCompleted.v1") == [pending.completed.to_dict()]
    assert not invocation_rows(owner, "ProductionDispatchAttempt.v1")


@pytest.mark.parametrize("field,value", [("schema_version", "StageSpec.v2"),
    ("consumer_id", "wrong:consumer"), ("route_id", "a10-m05-content-brief")])
def test_local_wrong_schema_consumer_route_rejected(local_case, field, value):
    result, refs, make_owner = local_case
    first = make_owner()
    pending = first.complete_a10(result, **refs, dispatch=False)
    wire = pending.completed.to_dict()
    wire["spec"][field] = value
    with sqlite3.connect(first._state_dir / "production.sqlite") as db:
        db.execute("UPDATE outbox SET completed=?", (json.dumps(wire),))
    with pytest.raises(OrchestrationError):
        make_owner().resume_local()
    assert not invocation_rows(first, "ProductionDispatchAttempt.v1")


def test_local_caller_source_and_revoked_authorization(local_case):
    result, refs, make_owner = local_case
    with pytest.raises(OrchestrationError, match="SOURCE_SHA_MISMATCH"):
        make_owner(source_sha="c" * 40).complete_a10(result, **refs)
    with pytest.raises(OrchestrationError, match="LOCAL_AUTHORIZATION_REQUIRED"):
        make_owner(local_authorization=None).complete_a10(result, **refs)
    first = make_owner()
    first.complete_a10(result, **refs, dispatch=False)
    first.revoke_local_authorization()
    with pytest.raises(OrchestrationError, match="LOCAL_AUTHORIZATION_REQUIRED"):
        first.resume_local()
    wrong = replace(make_owner()._local_authorization, authorization_ref="revoked-and-replaced")
    with pytest.raises(OrchestrationError, match="ROUTE_NOT_AUTHORIZED"):
        make_owner(local_authorization=wrong).resume_local()
    assert not invocation_rows(first, "ProductionDispatchAttempt.v1")


@pytest.mark.parametrize("restore", [True, False])
def test_local_only_preinvocation_missing_input_has_bounded_safe_retry(local_case, restore):
    result, refs, make_owner = local_case
    first = make_owner(max_attempts=2)
    first.complete_a10(result, **refs, dispatch=False)
    raw = refs["brief_ref"].read_bytes()
    refs["brief_ref"].unlink()
    actual = first.resume_local()[0]
    assert actual.status == "RETRY_SAFE" and actual.event is None
    assert not invocation_rows(first, "ProductionDispatchAttempt.v1")
    if restore:
        refs["brief_ref"].write_bytes(raw)
    second = make_owner(max_attempts=2)
    terminal = second.resume_local()[0]
    assert terminal.status == ("SUCCESS" if restore else "FAILED")
    assert second.resume_local() == (terminal,)


def test_original_p0_wire_records_remain_readable():
    completed, input_value, _ = stage()
    receipt = receipt_after_offline_execution(completed, input_value)
    wire = completed.to_dict()
    assert "local_handoff" not in wire["spec"]
    assert ProductionStageCompleted.from_dict(wire) == completed
    wire = receipt.to_dict()
    assert not {"outcome", "artifact_ref"} & wire.keys()
    assert "local_handoff" not in wire["spec"]
    assert ProductionStageReceipt.from_dict(wire) == receipt


@pytest.mark.parametrize("change", [{"schema_version": "A10AutomationResult.v2"}, {"status": "WAIT"},
    {"reason": "NO_SUITABLE_OPPORTUNITY"}, {"next_step": "UNKNOWN"}, {"source_sha": "c" * 40},
    {"brief_sha256": "0" * 64}, {"event_id": "a10event-" + "0" * 64}])
def test_local_untrusted_or_nonterminal_producer_cannot_self_authorize(local_case, change):
    result, refs, make_owner = local_case
    owner = make_owner()
    with pytest.raises(OrchestrationError):
        owner.complete_a10({**result, **change}, **refs)
    assert not owner._state_dir.exists()


@pytest.mark.parametrize("failure", ["revoked", "artifact", "journal-version"])
def test_local_witness_reconciliation_rechecks_authority_output_and_schema(local_case, monkeypatch, failure):
    result, refs, make_owner = local_case
    first = make_owner()
    def crash(*args):
        raise LocalCrash()
    monkeypatch.setattr(first, "_finish_local", crash)
    with pytest.raises(LocalCrash):
        first.complete_a10(result, **refs)
    assert len(invocation_rows(first, "A03LocalInvocation.v1")) == 1
    if failure == "revoked":
        second = make_owner(local_authorization=None)
    elif failure == "artifact":
        witness = invocation_rows(first, "A03LocalInvocation.v1")[0]
        Path(witness["artifact_ref"]).write_bytes(b"altered output")
        second = make_owner()
    else:
        with sqlite3.connect(first._state_dir / "production.sqlite") as db:
            db.execute("PRAGMA user_version=2")
        second = make_owner()
    with pytest.raises(OrchestrationError):
        second.resume_local()
    assert not invocation_rows(first, "ProductionStageReceipt.v1")


_A03_HELPERS = None


def _a03_helpers():
    global _A03_HELPERS
    if _A03_HELPERS is None:
        spec = importlib.util.spec_from_file_location(
            "a03_upgrade_v11_helpers", ROOT / "tests/test_a03_upgrade_v11.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _A03_HELPERS = mod
    return _A03_HELPERS


def _make_r4_cli(host_root: Path, monkeypatch):
    from ai_commerce.creative import (
        CodexCliCreativeClient, LiveCreativeReasoner, ModelConfiguration,
        ReasoningRequest, StructuredModelRequest,
    )

    h = _a03_helpers()
    cli = h.FakeInstalledCreativeCLI(host_root)
    r2_sampler = h.CycleFakeClient()
    r3_sampler = h.ProductionFakeClient(silent=True, variant="r4-v1")

    def dispatch_packet(packet):
        req = packet["request"]
        creative = ReasoningRequest.from_dict(req["creative"])
        model_req = StructuredModelRequest(ModelConfiguration.from_dict(req["config"]), creative)
        if creative.production is not None:
            return json.loads(r3_sampler.complete(model_req).output_json)
        val = json.loads(r2_sampler.complete(model_req).output_json)
        if creative.operation == "DEVELOP":
            val["edits"] = {n: val["edits"].get(n) for n in ("family", "title", "premise", "visual_direction")}
        return val

    cli.creative_output = dispatch_packet
    h.install_fake_popen(monkeypatch, cli)
    reasoner = LiveCreativeReasoner(cli.config, CodexCliCreativeClient(cli.capability(), runner=None))
    return h, cli, r3_sampler, reasoner


@pytest.fixture(scope="module")
def _a03_r3_template(tmp_path_factory):
    """Immutable prebuilt offline simulated native CLI R2/R3 store template.

    Built once per worker via the real offline install_fake_popen R2/R3 path
    with network sockets blocked; each test receives an isolated filesystem copy
    so mutations/tampers never leak across tests while preserving exact bytes.
    """
    from ai_commerce.creative import (
        CreativeSidecarStore, InvocationAuthorization,
        run_creative_cycle, run_creative_production,
    )

    template_root = tmp_path_factory.mktemp("a03-r3-template")
    creative_dir = template_root / "creative"
    with pytest.MonkeyPatch.context() as mp:
        def deny(*args, **kwargs):
            raise AssertionError("Network attempted")
        mp.setattr(socket, "socket", deny)
        mp.setattr(socket, "create_connection", deny)
        h, cli, _, reasoner = _make_r4_cli(template_root / "synthetic-cli-host", mp)
        cli_grant = InvocationAuthorization(True, True, "fixture:r4-cli-grant")
        brief = {
            **intake_brief("Im lặng hoàn toàn — thơ thị giác về mưa Sài Gòn, không sản phẩm hay CTA."),
            "product_ref": None, "cta": None, "audio": {"mode": "silence"},
        }
        sidecars = CreativeSidecarStore(creative_dir)
        cycle = run_creative_cycle(
            h.cycle_job(brief=brief, seed_options={"configuration": cli.config, "purpose": "PRODUCTION"}),
            sidecars, reasoner, authorization=cli_grant,
        )
        assert cycle.status == "CREATIVE_COMPLETE"
        prod_job = h.production_job(
            cycle,
            journey_id="r4:journey", cycle_id="r4:cycle", job_id="r4:prod-1", attempt=1,
            configuration=cli.config, purpose="PRODUCTION",
        )
        prod_result = run_creative_production(prod_job, sidecars, reasoner, authorization=cli_grant)
        assert prod_result.status == "CREATIVE_COMPLETE" and prod_result.package is not None

    source_sha = "d" * 40
    local_grant = LocalAuthorization(
        journey_id=prod_job.journey_id, cycle_id=prod_job.cycle_id, job_id=prod_job.job_id,
        source_sha=source_sha, authorization_ref="fixture:r4-a04-grant", route_id="a03-to-a04",
    )
    return {
        "creative_dir": creative_dir,
        "cli_grant": cli_grant,
        "brief": brief,
        "cycle": cycle,
        "job": prod_job,
        "result": prod_result,
        "source_sha": source_sha,
        "grant": local_grant,
    }


@pytest.fixture
def a03_r3_case(tmp_path, monkeypatch, _a03_r3_template):
    """Per-test isolated copy of the offline simulated native CLI R3 package fixture.

    Copies the immutable template store into tmp_path/creative and binds a fresh
    isolated fake CLI host so each test can freely tamper, delete, or run new
    R3 revisions without affecting sibling tests.
    """
    from ai_commerce.creative import CreativeSidecarStore

    creative_root = tmp_path / "creative"
    shutil.copytree(_a03_r3_template["creative_dir"], creative_root)
    h, cli, r3_sampler, reasoner = _make_r4_cli(tmp_path / "synthetic-cli-host", monkeypatch)
    sidecars = CreativeSidecarStore(creative_root)
    source_sha = _a03_r3_template["source_sha"]
    local_grant = _a03_r3_template["grant"]

    def make_owner(**changes):
        return ProductionPipelineOrchestrator(**{
            "state_dir": tmp_path / "production-r4",
            "source_sha": source_sha,
            "local_authorization": local_grant,
            **changes,
        })

    return {
        "helpers": h,
        "cli": cli,
        "r3_sampler": r3_sampler,
        "cli_grant": _a03_r3_template["cli_grant"],
        "reasoner": reasoner,
        "brief": copy.deepcopy(_a03_r3_template["brief"]),
        "sidecars": sidecars,
        "cycle": _a03_r3_template["cycle"],
        "job": _a03_r3_template["job"],
        "result": _a03_r3_template["result"],
        "source_sha": source_sha,
        "grant": local_grant,
        "make_owner": make_owner,
    }


def test_r4_a03_to_a04_verified_package_handoff_receipt_and_idempotent_replay(a03_r3_case, monkeypatch):
    from ai_commerce.media import generation as mg

    real_accept, calls = mg.accept_creative_production_package, []

    def spy_accept(**kwargs):
        calls.append(kwargs)
        return real_accept(**kwargs)

    monkeypatch.setattr(mg, "accept_creative_production_package", spy_accept)
    sidecars = a03_r3_case["sidecars"]
    prod_result = a03_r3_case["result"]
    pkg = prod_result.package
    owner = a03_r3_case["make_owner"]()
    actual = owner.complete_a03(prod_result, store=sidecars)
    assert actual.status == "SUCCESS"
    assert actual.accepted_consumer == mg.A04_CREATIVE_CONSUMER_ID
    assert actual.event.receipt.outcome == "SUCCESS"
    assert actual.event.receipt.consumer_executed is True
    assert actual.event.receipt.execution_ref.startswith("local:a04:")
    assert actual.event.receipt.output_sha256 == pkg.digest
    assert actual.completed.spec.output_contract == "A03CreativeProductionPackage.v1"
    assert actual.completed.spec.package_handoff.source_sha == a03_r3_case["source_sha"]
    assert len(calls) == 1

    receipt = actual.receiver_receipt
    assert isinstance(receipt, mg.A04CreativeHandoffReceipt)
    assert receipt.schema_version == "A04CreativeHandoffReceipt.v1"
    assert receipt.status == "SOURCE_HANDOFF_VERIFIED"
    assert receipt.media_execution == "NOT_ATTEMPTED"
    assert receipt.factual_status == "UNVERIFIED"
    assert receipt.semantic_approval == "NOT_ATTEMPTED"
    assert receipt.qa_status == "UNVERIFIED"
    assert receipt.pulse_execution == "NOT_ATTEMPTED"
    assert receipt.rights_status == "UNKNOWN"
    assert receipt.audio_mode == "SILENCE"
    assert receipt.package_id == pkg.package_id
    assert receipt.revision == 1 and receipt.parent_package_digest is None
    assert receipt.package_digest == pkg.digest
    assert receipt.source_sha == a03_r3_case["source_sha"]
    assert receipt.a03_result_digest == prod_result.digest
    assert receipt.journey_id == prod_result.job.journey_id
    assert receipt.cycle_id == prod_result.job.cycle_id
    assert receipt.job_id == prod_result.job.job_id
    assert receipt.attempt == prod_result.job.attempt
    assert receipt.actor_ref == f"AI:reasoning:{pkg.reasoning_receipt_digest}"
    assert receipt.consumer_id == mg.A04_CREATIVE_CONSUMER_ID
    assert receipt.brief_id == pkg.brief_id and receipt.source_brief_digest == pkg.source_brief_digest
    assert receipt.selection_id == pkg.selected_concept.selection_id
    assert receipt.concept_digest == pkg.selected_concept.concept_digest
    assert receipt.cycle_result_digest == pkg.lineage.cycle_result_digest
    assert mg.load_a04_handoff_receipt(actual.event.receipt.artifact_ref) == receipt

    serialized = json.dumps(actual.event.to_dict()) + json.dumps(receipt.to_dict())
    for forbidden in ("MEDIA_GENERATED", "RIGHTS_GRANTED", "CREATIVE_QA_PASSED", "PULSE_COMPLETE"):
        assert forbidden not in serialized

    reopened = a03_r3_case["make_owner"]()
    assert reopened.complete_a03(prod_result, store=sidecars) == actual
    assert reopened.resume_local() == (actual,)
    assert len(calls) == 1

    with sqlite3.connect(owner._state_dir / "production.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        kinds = [row[0] for row in db.execute("SELECT kind FROM journal ORDER BY seq")]
    assert kinds.index("ProductionStageCompleted.v1") < kinds.index("ProductionStageDispatched.v1")
    assert kinds.index("ProductionStageDispatched.v1") < kinds.index("A04LocalInvocation.v1")
    assert kinds.index("A04LocalInvocation.v1") < kinds.index("ProductionStageReceipt.v1")
    for kind in ("ProductionStageCompleted.v1", "ProductionDispatchAttempt.v1",
                 "A04LocalInvocation.v1", "ProductionStageReceipt.v1", "ProductionStageDispatched.v1"):
        assert len(invocation_rows(owner, kind)) == 1

    with pytest.raises(OrchestrationError, match="COMPLETED_NOT_FOUND"):
        reopened.record_receipt(actual.event.receipt)


def test_r4_incomplete_native_attestation_creates_no_completion_or_dispatch(a03_r3_case, monkeypatch):
    from ai_commerce.media import generation as mg

    monkeypatch.setattr(mg, "accept_creative_production_package", lambda **_: pytest.fail("A04 invoked"))
    result, sidecars = a03_r3_case["result"], a03_r3_case["sidecars"]
    owner = a03_r3_case["make_owner"]()
    for field in ("cli_schema_json", "cli_stdout_base64"):
        wire = result.to_dict()
        reasoning = wire["stage"]["reasoning"]
        response = json.loads(reasoning["response_json"])
        del response[field]
        reasoning["response_json"] = json.dumps(response)
        with pytest.raises(OrchestrationError, match="PRODUCER_CONTRACT_INVALID"):
            owner.complete_a03(wire, store=sidecars)

    wire = result.to_dict()
    wire["authorization"]["authorization_ref"] = "fixture:rebound-grant"
    with pytest.raises(OrchestrationError, match="PRODUCER_CONTRACT_INVALID"):
        owner.complete_a03(wire, store=sidecars)
    assert not owner._state_dir.exists()


@pytest.mark.parametrize(("provider", "failure", "explicit_agy"), [
    ("antigravity-cli", "AGY_AUTHENTICATED_CLI_SESSION_UNAVAILABLE", False),
    ("antigravity-cli", "AGY_AUTHENTICATED_CLI_SESSION_UNAVAILABLE", True),
    ("chatgpt-brain-cdp", "BRAIN_CDP_ORIGIN_AND_TURN_AUTHORITY_UNAVAILABLE", False),
])
def test_c1_unavailable_r3_provider_cannot_publish_a04_handoff(
    a03_r3_case, tmp_path, monkeypatch, provider, failure, explicit_agy,
):
    from ai_commerce.creative import (
        AIReasoningTask, AntigravityCliCreativeClient, LiveCreativeReasoner, ModelConfiguration,
        run_creative_production,
    )
    from ai_commerce.creative import live as creative_live
    from ai_commerce.creative import v2 as creative_v2
    from ai_commerce.media import generation as mg

    monkeypatch.setattr(mg, "accept_creative_production_package", lambda **_: pytest.fail("A04 invoked"))
    sidecars = a03_r3_case["sidecars"]
    owner = a03_r3_case["make_owner"]()
    grant = a03_r3_case["cli_grant"]
    # The existing Codex client must never be tried as a fallback.
    config, reasoner = ModelConfiguration(provider), a03_r3_case["reasoner"]
    agy_calls = []
    if explicit_agy:
        config = ModelConfiguration(provider, "explicit-agy-model", "high")
        executable = tmp_path.resolve() / ("agy.exe" if os.name == "nt" else "agy")
        executable.write_bytes(b"synthetic AGY executable")
        client = AntigravityCliCreativeClient(str(executable), bytes_digest(executable.read_bytes()))
        reasoner = LiveCreativeReasoner(config, client)
    job = replace(a03_r3_case["job"], attempt=2, configuration=config)
    if explicit_agy:
        saved = a03_r3_case["result"].stage.reasoning
        request = creative_v2._production_request(job, a03_r3_case["cycle"], saved.request.source)
        turn = client.prepare_turn(AIReasoningTask(creative_v2._production_stage_job(job, request), grant))
        output = json.loads(json.loads(saved.response_json)["output_json"])
        envelope = {"task_sha256": turn.task.digest, "stream_nonce": turn.stream_nonce, "output": output}
        conversation_id = "050ca293-cd26-49fa-a980-bbe5426cb6b9"

        def process(argv, *, input, cwd, timeout, max_bytes, detach_terminal):
            agy_calls.append(argv[1])
            if argv[1:] == ["--version"]:
                return subprocess.CompletedProcess(argv, 0, b"2.41.0", b"")
            if argv[1:] == ["--help"]:
                help_text = (
                    b"  --print  Print mode\n  --model  Model\n  --effort  Effort\n  --json-schema  Schema\n"
                    b"  --input-format  stream-json\n  --output-format  stream-json\n"
                    b"  --mode  Plan mode\n  --sandbox  Terminal sandbox\n"
                )
                return subprocess.CompletedProcess(argv, 0, help_text, b"")
            assert argv[1] == "--input-format" and json.loads(input)["event"] == "user"
            stdout = b"".join(canonical_bytes(event) + b"\n" for event in (
                {"event": "init", "conversation_id": conversation_id, "init": {
                    "cwd": cwd, "tools": [], "permission_mode": "strict",
                    "model": config.model, "json_schema": turn.output_schema,
                    "authenticated": True, "session": "fixture:spoof",
                }},
                {"event": "step_update", "step_update": {
                    "conversation_id": conversation_id, "step_index": 0, "state": "DONE", "step_type": "user_input",
                }},
                {"event": "result", "result": {
                    "conversation_id": conversation_id, "status": "SUCCESS", "response": json.dumps(envelope) + "\n",
                    "num_turns": 1, "duration_seconds": 0.1, "structured_output": envelope,
                    "json_schema": turn.output_schema,
                }},
            ))
            return subprocess.CompletedProcess(argv, 0, stdout, b"")

        monkeypatch.setattr(creative_live, "_bounded_cli_process", process)
        assert not agy_calls
        receipt = client.invoke_native_turn(turn, authorization=grant)
        assert (receipt.status, receipt.effect, receipt.terminal_status, receipt.error_code) == (
            "HOLD", "UNKNOWN_EFFECT", "SUCCESS", failure,
        )
        assert receipt.observed_model == config.model and receipt.observed_usage is None
        assert receipt.observed_session == "UNKNOWN"
        with pytest.raises(OrchestrationError, match="PRODUCER_SCHEMA"):
            owner.complete_a03(receipt, store=sidecars)
    result = run_creative_production(job, sidecars, reasoner, authorization=grant, production=owner)
    assert (result.status, result.failure, result.package, result.stage) == ("HOLD", failure, None, None)
    assert run_creative_production(job, sidecars, reasoner, authorization=grant, production=owner) == result
    assert a03_r3_case["cli"].calls == [] and owner.last_result is None
    assert agy_calls == (["--version", "--help", "--input-format"] if explicit_agy else [])
    with pytest.raises(OrchestrationError, match="PRODUCER_NOT_COMPLETE"):
        owner.complete_a03(result, store=sidecars)

    # Rebinding a simulated Codex response and execution metadata cannot
    # introduce a verifier for either new provider.
    wire = a03_r3_case["result"].to_dict()
    reasoning = wire["stage"]["reasoning"]
    response = json.loads(reasoning["response_json"])
    reasoning["configuration"]["provider"] = provider
    reasoning["observation"]["provider"] = provider
    response["observation"]["provider"] = provider
    reasoning["response_json"] = json.dumps(response)
    with pytest.raises(OrchestrationError, match="PRODUCER_CONTRACT_INVALID"):
        owner.complete_a03(wire, store=sidecars)
    assert not owner._state_dir.exists()


def test_r4_run_creative_production_opt_in_seam_and_no_reinference_replay(a03_r3_case, tmp_path, monkeypatch):
    from ai_commerce.creative import CreativeValidationError, run_creative_cycle, run_creative_production
    from ai_commerce.media import generation as mg

    h = a03_r3_case["helpers"]
    cli = a03_r3_case["cli"]
    sidecars = a03_r3_case["sidecars"]
    prod_job = a03_r3_case["job"]
    prod_result = a03_r3_case["result"]

    # Wrong production owner type rejected before touching store or CLI.
    with pytest.raises(CreativeValidationError, match="ProductionPipelineOrchestrator"):
        run_creative_production(prod_job, sidecars, production={"fake": True})

    # Replaying previously saved CREATIVE_COMPLETE result resumes A04 delivery with zero CLI calls.
    calls_before = len(cli.calls)
    a04_calls = []
    real_accept = mg.accept_creative_production_package

    def spy_accept(**kwargs):
        a04_calls.append(kwargs)
        return real_accept(**kwargs)

    monkeypatch.setattr(mg, "accept_creative_production_package", spy_accept)
    owner = a03_r3_case["make_owner"]()
    replayed = run_creative_production(prod_job, sidecars, production=owner)
    assert replayed == prod_result
    assert len(cli.calls) == calls_before
    assert owner.last_result is not None and owner.last_result.status == "SUCCESS"
    assert owner.last_result.receiver_receipt.status == "SOURCE_HANDOFF_VERIFIED"
    assert len(a04_calls) == 1

    # Second replay calls neither CLI nor A04 again.
    reopened = a03_r3_case["make_owner"]()
    assert run_creative_production(prod_job, sidecars, production=reopened) == prod_result
    assert len(cli.calls) == calls_before and len(a04_calls) == 1
    assert reopened.last_result == owner.last_result

    # TEST_ONLY_MOCK / HOLD / STOPPED never dispatch through production seam.
    mock_sidecars = h.CreativeSidecarStore(tmp_path / "mock-seam")
    mock_cycle = run_creative_cycle(h.cycle_job(), mock_sidecars, h.cycle_adapter(h.CycleFakeClient()))
    for label, client in (("mock", h.ProductionFakeClient()),
                          ("hold", h.ProductionFakeClient(fault="timeout")),
                          ("stop", h.ProductionFakeClient(fault="refusal"))):
        mj = h.production_job(mock_cycle, journey_id="m:j", cycle_id="m:c", job_id=f"m:{label}")
        mgrant = LocalAuthorization(journey_id="m:j", cycle_id="m:c", job_id=f"m:{label}",
                                    source_sha=a03_r3_case["source_sha"],
                                    authorization_ref="fixture:m-grant", route_id="a03-to-a04")
        mowner = ProductionPipelineOrchestrator(state_dir=tmp_path / f"m-orch-{label}",
                                                source_sha=a03_r3_case["source_sha"],
                                                local_authorization=mgrant)
        mres = run_creative_production(mj, mock_sidecars, h.cycle_adapter(client), production=mowner)
        assert mres.status != "CREATIVE_COMPLETE"
        assert mowner.last_result is None
    assert len(a04_calls) == 1


def test_r4_editable_package_revisions_retain_immutable_parent_and_independent_keys(a03_r3_case, monkeypatch):
    from ai_commerce.creative import ActorRef, revise_production_package, run_creative_production
    from ai_commerce.media import generation as mg

    sidecars = a03_r3_case["sidecars"]
    prod_result = a03_r3_case["result"]
    pkg_r1 = prod_result.package
    r1_bytes = sidecars.production_package_path(pkg_r1.package_id, 1).read_bytes()

    real_accept, calls = mg.accept_creative_production_package, []

    def spy_accept(**kwargs):
        calls.append((kwargs["package_id"], kwargs["revision"]))
        return real_accept(**kwargs)

    monkeypatch.setattr(mg, "accept_creative_production_package", spy_accept)
    owner = a03_r3_case["make_owner"]()
    res_r1 = owner.complete_a03(prod_result, store=sidecars)
    assert res_r1.status == "SUCCESS" and res_r1.receiver_receipt.revision == 1

    # Direct HUMAN edit creates immutable revision 2 retaining parent r1 without new inference.
    pkg_r2 = revise_production_package(
        pkg_r1,
        {"narrative_intention": "Chỉnh nhịp ánh sáng mưa tĩnh lặng hơn."},
        edit_reason="Human refined silent visual cadence",
        actor=ActorRef("HUMAN", "human:director"),
    )
    sidecars.save_production_package(pkg_r2)
    assert sidecars.production_package_path(pkg_r1.package_id, 1).read_bytes() == r1_bytes

    res_r2 = owner.complete_a03(prod_result, store=sidecars, package=pkg_r2)
    assert res_r2.status == "SUCCESS"
    assert res_r2.completed.spec.idempotency_key != res_r1.completed.spec.idempotency_key
    assert res_r2.receiver_receipt.revision == 2
    assert res_r2.receiver_receipt.parent_package_digest == pkg_r1.digest
    assert res_r2.receiver_receipt.actor_ref == "HUMAN:human:director"
    assert res_r2.receiver_receipt.reasoning_receipt_digest is None
    assert res_r2.receiver_receipt.media_execution == "NOT_ATTEMPTED"
    assert calls == [(pkg_r1.package_id, 1), (pkg_r1.package_id, 2)]

    # Reopened instance resumes both revisions idempotently with zero extra A04 calls.
    reopened = a03_r3_case["make_owner"]()
    assert reopened.resume_local() == (res_r1, res_r2)
    assert len(calls) == 2

    # Bounded AI-assisted revision 3 retains parent r2 with its own independent production result.
    a03_r3_case["r3_sampler"].variant = "r4-v3-ai"
    r3_job = a03_r3_case["helpers"].production_job(
        a03_r3_case["cycle"],
        journey_id="r4:journey", cycle_id="r4:cycle", job_id="r4:prod-3", attempt=1,
        configuration=a03_r3_case["cli"].config, purpose="PRODUCTION",
        parent_package=pkg_r2, edit_reason="AI lighting continuity refinement",
    )
    r3_grant = LocalAuthorization(
        journey_id="r4:journey", cycle_id="r4:cycle", job_id="r4:prod-3",
        source_sha=a03_r3_case["source_sha"], authorization_ref="fixture:r4-a04-grant-r3",
        route_id="a03-to-a04",
    )
    r3_owner = a03_r3_case["make_owner"](local_authorization=r3_grant)
    r3_result = run_creative_production(
        r3_job, sidecars, a03_r3_case["reasoner"],
        authorization=a03_r3_case["cli_grant"], production=r3_owner,
    )
    assert r3_result.status == "CREATIVE_COMPLETE" and r3_result.package.revision == 3
    assert r3_owner.last_result.status == "SUCCESS"
    assert r3_owner.last_result.receiver_receipt.revision == 3
    assert r3_owner.last_result.receiver_receipt.parent_package_digest == pkg_r2.digest
    assert r3_owner.last_result.completed.spec.idempotency_key not in {
        res_r1.completed.spec.idempotency_key, res_r2.completed.spec.idempotency_key,
    }


def test_r4_outbox_commit_before_dispatch_and_active_lease(a03_r3_case, monkeypatch):
    sidecars = a03_r3_case["sidecars"]
    prod_result = a03_r3_case["result"]
    first = a03_r3_case["make_owner"]()
    pending = first.complete_a03(prod_result, store=sidecars, dispatch=False)
    assert pending.status == "PENDING" and pending.event is None
    assert invocation_rows(first, "ProductionStageCompleted.v1") == [pending.completed.to_dict()]
    assert not invocation_rows(first, "A04LocalInvocation.v1")
    assert not invocation_rows(first, "ProductionStageDispatched.v1")

    key = pending.completed.spec.idempotency_key
    second = a03_r3_case["make_owner"]()
    real_invoke = first._invoke_a04
    observations = []

    def observe_lease(completed, raw):
        observations.append(second.dispatch_local(key))
        return real_invoke(completed, raw)

    monkeypatch.setattr(first, "_invoke_a04", observe_lease)
    actual = first.dispatch_local(key)
    assert observations[0].status == "RUNNING" and observations[0].event is None
    assert actual.status == "SUCCESS" and second.dispatch_local(key) == actual
    assert len(invocation_rows(first, "ProductionDispatchAttempt.v1")) == 1


@pytest.mark.parametrize("window", ["after-accept", "after-witness"])
def test_r4_crash_windows_never_blindly_reinvoke_a04(a03_r3_case, monkeypatch, window):
    from ai_commerce.media import generation as mg

    sidecars = a03_r3_case["sidecars"]
    prod_result = a03_r3_case["result"]
    first, calls = a03_r3_case["make_owner"](), []
    real_accept = mg.accept_creative_production_package

    def spy_accept(**kwargs):
        calls.append(kwargs["package_id"])
        return real_accept(**kwargs)

    monkeypatch.setattr(mg, "accept_creative_production_package", spy_accept)
    real_invoke = first._invoke_a04

    def invoke(completed, raw):
        witness = real_invoke(completed, raw)
        if window == "after-accept":
            raise LocalCrash()
        return witness

    monkeypatch.setattr(first, "_invoke_a04", invoke)
    if window == "after-witness":
        def crash(*args):
            raise LocalCrash()
        monkeypatch.setattr(first, "_finish_local", crash)

    with pytest.raises(LocalCrash):
        first.complete_a03(prod_result, store=sidecars)
    assert not invocation_rows(first, "ProductionStageReceipt.v1")
    monkeypatch.setattr("ai_commerce.production_orchestration.orchestrator.time.time", lambda: 10**12)
    second = a03_r3_case["make_owner"]()
    recovered = second.resume_local()[0]
    assert recovered.status == ("UNKNOWN" if window == "after-accept" else "SUCCESS")
    assert second.resume_local() == (recovered,)
    assert len(calls) == 1
    assert len(invocation_rows(first, "A04LocalInvocation.v1")) == (window == "after-witness")
    assert len(invocation_rows(first, "ProductionStageReceipt.v1")) == (window == "after-witness")


@pytest.mark.parametrize("failure,expected", [("reject", "UNKNOWN"), ("alter", "UNKNOWN"), ("unknown", "UNKNOWN")])
def test_r4_receiver_failure_and_unknown_effect_never_mint_success(a03_r3_case, monkeypatch, failure, expected):
    from ai_commerce.media import generation as mg

    sidecars = a03_r3_case["sidecars"]
    prod_result = a03_r3_case["result"]
    real_accept, calls = mg.accept_creative_production_package, []

    def broken(**kwargs):
        calls.append(kwargs["package_id"])
        if failure == "reject":
            raise ValueError("synthetic A04 validation rejection")
        if failure == "unknown":
            raise RuntimeError("synthetic A04 ambiguous I/O error")
        real_accept(**kwargs)
        return {"forged": True, "status": "SOURCE_HANDOFF_VERIFIED"}

    monkeypatch.setattr(mg, "accept_creative_production_package", broken)
    first = a03_r3_case["make_owner"]()
    actual = first.complete_a03(prod_result, store=sidecars)
    assert actual.status == expected and actual.event is None and actual.accepted_consumer is None
    assert actual.reason == "RECEIVER_UNRESOLVED"
    # The altered return follows a real durable A04 receipt write: that effect
    # must remain UNKNOWN, not a falsely proven pre-effect FAILED.
    receipts = list((first._state_dir / "a04-handoffs").glob("*.json"))
    assert len(receipts) == (1 if failure == "alter" else 0)
    assert a03_r3_case["make_owner"]().resume_local() == (actual,)
    assert len(calls) == 1
    assert not invocation_rows(first, "ProductionStageReceipt.v1")


@pytest.mark.parametrize("restore", [True, False])
def test_r4_preinvocation_missing_package_has_bounded_safe_retry(a03_r3_case, restore):
    sidecars = a03_r3_case["sidecars"]
    prod_result = a03_r3_case["result"]
    pkg = prod_result.package
    first = a03_r3_case["make_owner"](max_attempts=2)
    first.complete_a03(prod_result, store=sidecars, dispatch=False)
    pkg_path = sidecars.production_package_path(pkg.package_id, pkg.revision)
    saved_bytes = pkg_path.read_bytes()
    pkg_path.unlink()
    retry = first.resume_local()[0]
    assert retry.status == "RETRY_SAFE" and retry.event is None
    assert not invocation_rows(first, "ProductionDispatchAttempt.v1")
    if restore:
        pkg_path.write_bytes(saved_bytes)
    second = a03_r3_case["make_owner"](max_attempts=2)
    terminal = second.resume_local()[0]
    assert terminal.status == ("SUCCESS" if restore else "FAILED")
    assert second.resume_local() == (terminal,)


def test_r4_negative_rejections_intake_mock_hold_stopped_paths_pins_and_tamper(a03_r3_case, local_case, tmp_path, monkeypatch):
    from ai_commerce.creative import ActorRef, revise_production_package, run_creative_cycle, run_creative_production

    h = a03_r3_case["helpers"]
    sidecars = a03_r3_case["sidecars"]
    prod_result = a03_r3_case["result"]
    pkg = prod_result.package
    owner = a03_r3_case["make_owner"]()

    # 1) Intake-only ContentBrief rejected as creative success.
    with pytest.raises(OrchestrationError, match="PRODUCER_SCHEMA"):
        owner.complete_a03(a03_r3_case["brief"], store=sidecars)

    # 2) TEST_ONLY_MOCK, HOLD, STOPPED A03 results rejected.
    base_mock_dir = tmp_path / "neg-mock-base"
    base_mock_sidecars = h.CreativeSidecarStore(base_mock_dir)
    mock_cycle = run_creative_cycle(h.cycle_job(), base_mock_sidecars, h.cycle_adapter(h.CycleFakeClient()))
    for label, client in (("mock", h.ProductionFakeClient()),
                          ("hold", h.ProductionFakeClient(fault="timeout")),
                          ("stop", h.ProductionFakeClient(fault="refusal"))):
        case_dir = tmp_path / f"neg-mock-{label}"
        shutil.copytree(base_mock_dir, case_dir)
        mock_sidecars = h.CreativeSidecarStore(case_dir)
        mj = h.production_job(mock_cycle, journey_id="r4:journey", cycle_id="r4:cycle", job_id="r4:prod-1")
        mres = run_creative_production(mj, mock_sidecars, h.cycle_adapter(client))
        with pytest.raises(OrchestrationError, match="PRODUCER_NOT_COMPLETE"):
            owner.complete_a03(mres, store=mock_sidecars)

    # 3) External / temp path outside sidecar store (e.g. GitHub issue artifact) rejected.
    temp_copy = tmp_path / "github-issue-artifact.json"
    temp_copy.write_bytes(sidecars.production_result_path(prod_result.digest).read_bytes())
    with pytest.raises(OrchestrationError, match="PRODUCER_REFERENCE_MISMATCH"):
        owner.complete_a03(prod_result, store=sidecars, result_ref=temp_copy)

    # 4) Cross-route grant mismatches fail closed.
    a10_grant = replace(a03_r3_case["grant"], route_id="a10-m05-to-a03")
    with pytest.raises(OrchestrationError, match="LOCAL_ROUTE_MISMATCH"):
        a03_r3_case["make_owner"](local_authorization=a10_grant).complete_a03(prod_result, store=sidecars)
    a10_result, a10_refs, make_a10_owner = local_case
    wrong_a10_grant = replace(make_a10_owner()._local_authorization, route_id="a03-to-a04")
    with pytest.raises(OrchestrationError, match="LOCAL_ROUTE_MISMATCH"):
        make_a10_owner(local_authorization=wrong_a10_grant).complete_a10(a10_result, **a10_refs)

    # 5) Mismatched source_sha or missing/revoked local authorization rejected.
    with pytest.raises(OrchestrationError, match="SOURCE_SHA_MISMATCH"):
        a03_r3_case["make_owner"](source_sha="e" * 40).complete_a03(prod_result, store=sidecars)
    with pytest.raises(OrchestrationError, match="LOCAL_AUTHORIZATION_REQUIRED"):
        a03_r3_case["make_owner"](local_authorization=None).complete_a03(prod_result, store=sidecars)

    # 6) Different source pins never silently replay each other's outbox items.
    done = owner.complete_a03(prod_result, store=sidecars)
    assert done.status == "SUCCESS"
    other_sha = "f" * 40
    other_grant = replace(a03_r3_case["grant"], source_sha=other_sha)
    other_owner = a03_r3_case["make_owner"](source_sha=other_sha, local_authorization=other_grant)
    assert other_owner.resume_local() == ()
    with pytest.raises(OrchestrationError, match="ROUTE_NOT_AUTHORIZED"):
        other_owner.dispatch_local(done.completed.spec.idempotency_key)

    # 7) Altered authorization_ref or revoked grant cannot resume/reconcile.
    altered_grant = replace(a03_r3_case["grant"], authorization_ref="fixture:altered-grant")
    with pytest.raises(OrchestrationError, match="ROUTE_NOT_AUTHORIZED"):
        a03_r3_case["make_owner"](local_authorization=altered_grant).resume_local()
    owner.revoke_local_authorization()
    with pytest.raises(OrchestrationError, match="LOCAL_AUTHORIZATION_REQUIRED"):
        owner.resume_local()

    # 8) Conflicting revision content for same (grant, package_id, revision) rejected.
    r2_a = revise_production_package(
        pkg, {"narrative_intention": "Variant A"}, edit_reason="A", actor=ActorRef("HUMAN", "human:a"),
    )
    sidecars.save_production_package(r2_a)
    r2_owner = a03_r3_case["make_owner"]()
    r2_done = r2_owner.complete_a03(prod_result, store=sidecars, package=r2_a)
    assert r2_done.status == "SUCCESS"
    # An unsaved conflicting revision must fail the immutable bytes pin before dispatch.
    r2_b = revise_production_package(
        pkg, {"narrative_intention": "Variant B"}, edit_reason="B", actor=ActorRef("HUMAN", "human:b"),
    )
    with pytest.raises(OrchestrationError, match="OUTPUT_BYTES_MISMATCH"):
        r2_owner.complete_a03(prod_result, store=sidecars, package=r2_b)

    # 9) Tampered durable A04 receipt file fails witness reconciliation closed.
    tampered_owner = a03_r3_case["make_owner"](state_dir=tmp_path / "r4-tamper-witness")
    monkeypatch.setattr(tampered_owner, "_finish_local", lambda *args: (_ for _ in ()).throw(LocalCrash()))
    with pytest.raises(LocalCrash):
        tampered_owner.complete_a03(prod_result, store=sidecars)
    witness = invocation_rows(tampered_owner, "A04LocalInvocation.v1")[0]
    Path(witness["artifact_ref"]).write_bytes(b"{\"tampered\":true}")
    with pytest.raises(OrchestrationError, match="INVOCATION_WITNESS_MISMATCH"):
        a03_r3_case["make_owner"](state_dir=tmp_path / "r4-tamper-witness").resume_local()
    assert not invocation_rows(tampered_owner, "ProductionStageReceipt.v1")

    # 10) Tampered outbox route/consumer/schema rejected before any A04 call.
    spoof_dir = tmp_path / "r4-spoof"
    spoof_owner = a03_r3_case["make_owner"](state_dir=spoof_dir)
    pending = spoof_owner.complete_a03(prod_result, store=sidecars, dispatch=False)
    for field, value in (("schema_version", "StageSpec.v2"), ("consumer_id", "spoof:consumer"),
                         ("route_id", "a03-creative-brief")):
        wire = pending.completed.to_dict()
        wire["spec"][field] = value
        with sqlite3.connect(spoof_dir / "production.sqlite") as db:
            db.execute("UPDATE outbox SET completed=?", (json.dumps(wire),))
        with pytest.raises(OrchestrationError):
            a03_r3_case["make_owner"](state_dir=spoof_dir).resume_local()
    assert not invocation_rows(spoof_owner, "ProductionDispatchAttempt.v1")

    # 11) Tampered package bytes after outbox commit fail closed as FAILED without invoking A04.
    bytes_dir = tmp_path / "r4-tamper-bytes"
    bytes_owner = a03_r3_case["make_owner"](state_dir=bytes_dir)
    bytes_owner.complete_a03(prod_result, store=sidecars, dispatch=False)
    pkg_path = sidecars.production_package_path(pkg.package_id, 1)
    original_pkg_bytes = pkg_path.read_bytes()
    try:
        pkg_path.write_bytes(original_pkg_bytes + b" ")
        failed = a03_r3_case["make_owner"](state_dir=bytes_dir).resume_local()[0]
        assert failed.status == "FAILED" and failed.reason == "INPUT_REJECTED" and failed.event is None
        assert not invocation_rows(bytes_owner, "ProductionDispatchAttempt.v1")
    finally:
        pkg_path.write_bytes(original_pkg_bytes)


def test_r4_p0_detached_a04_snapshot_never_dispatches_or_self_attests(a03_r3_case, monkeypatch):
    from ai_commerce.core.provenance import canonical_bytes as prov_bytes
    from ai_commerce.media import generation as mg

    prod_result = a03_r3_case["result"]
    pkg = prod_result.package
    source_bytes = prov_bytes(prod_result.to_dict())
    consumer, path = CONSUMER_ROUTES["a04-media-preparation"]
    spec = StageSpec(
        stage_id="A03.creative",
        route_id="a04-media-preparation",
        consumer_id=consumer,
        consumer_path=path,
        source_sha256=bytes_digest(source_bytes),
        input_sha256=bytes_digest(source_bytes),
        output_sha256=pkg.digest,
        idempotency_key="p0-a04-snapshot-1",
        output_contract="A03CreativeProductionPackage.v1",
        provenance=Provenance(
            actor_ref="fixture:a03", source_ref="fixture:a03-result",
            source_sha256=bytes_digest(source_bytes), evidence_type="SYNTHETIC_FIXTURE",
        ),
        authorization=RouteAuthorization(
            route_id="a04-media-preparation", consumer_id=consumer, consumer_path=path,
            authorization_ref="fixture:a04-p0-permit",
        ),
    )
    completed = ProductionStageCompleted(
        spec=spec, completion_ref="fixture:a04-completed",
        source_sha256=spec.source_sha256, input_sha256=spec.input_sha256, output_sha256=spec.output_sha256,
    )
    monkeypatch.setattr(mg, "accept_creative_production_package",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("P0 must never execute A04")))
    orch = ProductionPipelineOrchestrator(authorized_specs=((spec.authorization.authorization_ref, spec.sha256),))
    res = orch.record_completed(completed, source_bytes=source_bytes,
                                input_value=prod_result.to_dict(), output=pkg.to_dict())
    assert res.status == "NOT_DISPATCHED" and res.accepted_consumer is None
    untrusted_receipt = ProductionStageReceipt(
        spec=spec, completed_sha256=completed.sha256, output_sha256=pkg.digest,
        consumer_id=consumer, receipt_ref="fixture:a04-rec", execution_ref="fixture:a04-exec",
        provenance=replace(spec.provenance, actor_ref=consumer), consumer_executed=True,
    )
    with pytest.raises(OrchestrationError, match="RECEIPT_NOT_VERIFIED"):
        orch.record_receipt(untrusted_receipt)


def _w2_png_bytes(red: int = 120) -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, content: bytes) -> bytes:
        return (
            struct.pack(">I", len(content))
            + kind
            + content
            + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + bytes((red, 30, 180)) * 16) * 16))
        + chunk(b"IEND", b"")
    )


def _w2_require_windows_media() -> None:
    import sys

    assert sys.platform == "win32", "MATERIAL_UNPROVEN_WINDOWS_REQUIRED"
    assert shutil.which("ffmpeg") and shutil.which("ffprobe"), "MATERIAL_UNPROVEN_FFMPEG_REQUIRED"


@pytest.fixture
def w2_pulse_case(tmp_path, monkeypatch, _a03_r3_template):
    """Composed A03 (CREATIVE_COMPLETE) -> A04 (persisted receipt) -> PULSE offline fixture."""
    import time
    from ai_commerce.creative import CreativeSidecarStore, run_creative_production
    from ai_commerce.production_runtime import attended_worker
    from ai_commerce.production_runtime.contracts import (
        canonical_bytes as pulse_canonical_bytes,
        draft_authorization,
        seal_contract,
    )

    source_sha = "95f4f5d9d448410305fbfa809c25ea331008723c"
    journey_id = "journey:" + "11" * 16
    cycle_id = "cycle:w2-pulse-02"
    job_id = "job:w2-pulse-02"

    creative_root = tmp_path / "creative"
    shutil.copytree(_a03_r3_template["creative_dir"], creative_root)
    with pytest.MonkeyPatch.context() as mp:
        h, cli, _, reasoner = _make_r4_cli(tmp_path / "synthetic-cli-host", mp)
        sidecars = CreativeSidecarStore(creative_root)
        prod_job = h.production_job(
            _a03_r3_template["cycle"],
            journey_id=journey_id,
            cycle_id=cycle_id,
            job_id=job_id,
            attempt=1,
            configuration=cli.config,
            purpose="PRODUCTION",
        )
        a04_grant = LocalAuthorization(
            journey_id=journey_id,
            cycle_id=cycle_id,
            job_id=job_id,
            source_sha=source_sha,
            authorization_ref="fixture:w2-a04-grant",
            route_id="a03-to-a04",
        )
        a04_state_dir = tmp_path / "production-a04"
        a04_owner = ProductionPipelineOrchestrator(
            state_dir=a04_state_dir, source_sha=source_sha, local_authorization=a04_grant,
        )
        prod_result = run_creative_production(
            prod_job, sidecars, reasoner,
            authorization=_a03_r3_template["cli_grant"],
            production=a04_owner,
        )
    assert prod_result.status == "CREATIVE_COMPLETE" and prod_result.package is not None
    assert a04_owner.last_result is not None and a04_owner.last_result.status == "SUCCESS"
    a04_receipt = a04_owner.last_result.receiver_receipt
    a04_receipt_path = Path(a04_owner.last_result.event.receipt.artifact_ref)
    assert a04_receipt.audio_mode == "SILENCE"

    asset_dir = tmp_path / "private-assets"
    asset_dir.mkdir()
    png_path = asset_dir / "authorized-still.png"
    png_bytes = _w2_png_bytes(120)
    png_path.write_bytes(png_bytes)
    asset_sha = bytes_digest(png_bytes)
    asset_ref = "asset:" + asset_sha

    rights_claim = {
        "schema_version": "LocalStillRights.v1",
        "asset_sha256": asset_sha,
        "source_sha": source_sha,
        "scope": "OFFLINE_LOCAL_RENDER",
        "status": "GRANTED",
        "valid_from": 0,
        "expires_at": int(time.time()) + 3600,
    }
    rights_raw = pulse_canonical_bytes(rights_claim)
    rights_path = asset_dir / "rights-still.json"
    rights_path.write_bytes(rights_raw)
    rights_sha = bytes_digest(rights_raw)
    rights_ref = "rights:" + rights_sha

    directive_body = {
        "schema_version": "CreativeDirective.v1",
        "journey_id": journey_id,
        "source_sha": source_sha,
        "directive_version": 1,
        "creative": {
            "freeform": "Silent offline visual showcase, null product, no CTA.",
            "product_ref": None,
        },
        "scope": {
            "action_class": "EXPORT_DRAFT",
            "provider_ref": None,
            "account_ref": None,
            "surface": None,
            "asset_sha256": None,
        },
        "assets": [{
            "ref": asset_ref,
            "sha256": asset_sha,
            "evidence_type": "HUMAN_SUPPLIED",
            "rights": {"status": "GRANTED", "ref": rights_ref, "sha256": rights_sha},
        }],
        "action_classes": ["EXPORT_DRAFT"],
        "allowed_tools": [attended_worker.TOOL],
        "provenance": {
            "actor": "HUMAN",
            "actor_ref": "human:" + "4" * 32,
            "evidence_type": "HUMAN_SUPPLIED",
            "source_ref": "directive:" + "5" * 32,
            "source_sha": source_sha,
        },
        "authorization": draft_authorization(),
    }
    directive = seal_contract("CreativeDirective", directive_body)
    directive_path = tmp_path / "directive.json"
    directive_path.write_bytes(directive.raw)

    pulse_grant = LocalAuthorization(
        journey_id=journey_id,
        cycle_id=cycle_id,
        job_id=job_id,
        source_sha=source_sha,
        authorization_ref="fixture:w2-pulse-grant",
        route_id="a04-to-pulse-offline",
    )

    def make_pulse_owner(**changes):
        return ProductionPipelineOrchestrator(**{
            "state_dir": tmp_path / "production-pulse",
            "source_sha": source_sha,
            "local_authorization": pulse_grant,
            **changes,
        })

    call_kwargs = {
        "a04_receipt_ref": a04_receipt_path,
        "directive_ref": directive_path,
        "assets": (f"{asset_ref}={png_path}",),
        "rights": (f"{rights_ref}={rights_path}",),
        "asset_root": asset_dir,
        "package_ref": Path(a04_receipt.package_ref),
        "a03_result_ref": Path(a04_receipt.a03_result_ref),
        "cta_mode": "NONE",
        "cta_text": None,
    }
    return {
        "tmp_path": tmp_path,
        "source_sha": source_sha,
        "journey_id": journey_id,
        "cycle_id": cycle_id,
        "job_id": job_id,
        "a04_receipt": a04_receipt,
        "a04_receipt_path": a04_receipt_path,
        "directive": directive,
        "directive_body": directive_body,
        "directive_path": directive_path,
        "asset_dir": asset_dir,
        "png_path": png_path,
        "rights_path": rights_path,
        "rights_claim": rights_claim,
        "asset_ref": asset_ref,
        "rights_ref": rights_ref,
        "grant": pulse_grant,
        "make_owner": make_pulse_owner,
        "call_kwargs": call_kwargs,
    }


def test_w2_pulse02_composed_a03_a04_outbox_pulse_wait_brain_and_idempotent_dedup(w2_pulse_case, monkeypatch):
    _w2_require_windows_media()
    from ai_commerce.a04_pulse_handoff.handoff import PULSE_LOCAL_CONSUMER_ID
    from ai_commerce.production_runtime import attended_worker, receipt as runtime_receipt
    from ai_commerce.production_runtime.contracts import parse_contract, seal_contract

    real_receive, receive_calls = attended_worker.receive_a04_pulse_handoff, []
    real_a06, render_calls = attended_worker.service.run, []

    def spy_receive(**kwargs):
        receive_calls.append(kwargs["packet_path"])
        return real_receive(**kwargs)

    def spy_a06(*args, **kwargs):
        render_calls.append(args[1])
        return real_a06(*args, **kwargs)

    monkeypatch.setattr(attended_worker, "receive_a04_pulse_handoff", spy_receive)
    monkeypatch.setattr(attended_worker.service, "run", spy_a06)

    owner = w2_pulse_case["make_owner"]()
    actual = owner.complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    assert actual.status == "SUCCESS"
    assert actual.accepted_consumer == PULSE_LOCAL_CONSUMER_ID
    assert actual.event.receipt.outcome == "SUCCESS"
    assert actual.event.receipt.consumer_executed is True
    assert actual.event.receipt.execution_ref.startswith("local:pulse:")
    assert actual.completed.spec.output_contract == "A04PulseOfflineHandoffPacket.v1"
    assert len(receive_calls) == 1 and len(render_calls) == 1

    rec = actual.receiver_receipt
    assert rec["schema_version"] == "A04PulseReceiverReceipt.v1"
    assert rec["state"] == "WAIT_BRAIN"
    assert rec["human_attended"] is False
    assert rec["brain_decision"] is None
    assert rec["journal_sequence"] == 4
    assert rec["source_sha"] == w2_pulse_case["source_sha"]
    assert rec["q4_qa_ref"].startswith("q4:")
    assert rec["provider_network_calls"] == rec["paid_generation_calls"] == rec["publish_calls"] == 0

    receipt_file = Path(actual.event.receipt.artifact_ref)
    parsed_receipt = parse_contract("ProductJourneyReceipt", receipt_file.read_bytes()).to_dict()
    assert parsed_receipt["state"] == "WAIT_BRAIN"
    assert parsed_receipt["brain_decision"] is None
    assert parsed_receipt["effect"]["status"] == "NONE"
    assert parsed_receipt["network_calls_performed"] is False
    assert parsed_receipt["external_effects_performed"] is False
    assert any(pin["ref"] == rec["q4_qa_ref"] for pin in parsed_receipt["owner_evidence_refs"])

    evidence_file = receipt_file.parent / "autonomous-offline-evidence.json"
    parsed_evidence = runtime_receipt.parse_autonomous_evidence_packet(evidence_file.read_bytes())
    assert parsed_evidence["human_attended"] is False
    assert parsed_evidence["q4_technical_qa"]["status"] == "TECHNICAL_QA_PASSED"
    assert parsed_evidence["q4_technical_qa"]["ffprobe_full_decode"] is True
    assert parsed_evidence["q4_technical_qa"]["audio_mode"] == "SILENCE"
    assert parsed_evidence["q4_technical_qa"]["cta_mode"] == "NONE"

    # Reopened orchestrator replays exact logical dispatch idempotently with zero extra renders.
    reopened = w2_pulse_case["make_owner"]()
    assert reopened.complete_a04_pulse(**w2_pulse_case["call_kwargs"]) == actual
    assert reopened.resume_local() == (actual,)
    assert len(receive_calls) == 1 and len(render_calls) == 1

    # Persistent dedup rejects reusing the same A04 receipt with a bumped directive version.
    v2_body = copy.deepcopy(w2_pulse_case["directive_body"])
    v2_body["directive_version"] = 2
    v2_dir = seal_contract("CreativeDirective", v2_body)
    v2_path = w2_pulse_case["tmp_path"] / "directive-v2.json"
    v2_path.write_bytes(v2_dir.raw)
    with pytest.raises(OrchestrationError, match="DUPLICATE_LOGICAL_IDENTITY"):
        reopened.complete_a04_pulse(**{**w2_pulse_case["call_kwargs"], "directive_ref": v2_path})
    assert len(receive_calls) == 1 and len(render_calls) == 1

    # Outbox schema stays user_version=1 and records exact ordered journal sequence.
    with sqlite3.connect(owner._state_dir / "production.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        kinds = [row[0] for row in db.execute("SELECT kind FROM journal ORDER BY seq")]
    assert kinds.index("ProductionStageCompleted.v1") < kinds.index("ProductionStageDispatched.v1")
    assert kinds.index("ProductionStageDispatched.v1") < kinds.index("A04PulseLocalInvocation.v1")
    assert kinds.index("A04PulseLocalInvocation.v1") < kinds.index("ProductionStageReceipt.v1")
    for kind in ("ProductionStageCompleted.v1", "ProductionDispatchAttempt.v1",
                 "A04PulseLocalInvocation.v1", "ProductionStageReceipt.v1", "ProductionStageDispatched.v1"):
        assert len(invocation_rows(owner, kind)) == 1

    # Wire receipts cannot self-attest a durable W2 dispatch.
    with pytest.raises(OrchestrationError, match="COMPLETED_NOT_FOUND"):
        reopened.record_receipt(actual.event.receipt)


@pytest.mark.parametrize("window", ["before-dispatch", "after-reservation", "after-witness"])
def test_w2_pulse02_restart_windows_converge_without_double_render(w2_pulse_case, monkeypatch, window):
    _w2_require_windows_media()
    from ai_commerce.production_runtime import attended_worker

    real_a06, render_calls = attended_worker.service.run, []
    def spy_a06(*args, **kwargs):
        render_calls.append(1)
        return real_a06(*args, **kwargs)
    monkeypatch.setattr(attended_worker.service, "run", spy_a06)

    state_dir = w2_pulse_case["tmp_path"] / f"restart-{window}"
    first = w2_pulse_case["make_owner"](state_dir=state_dir)

    if window == "before-dispatch":
        pending = first.complete_a04_pulse(**w2_pulse_case["call_kwargs"], dispatch=False)
        assert pending.status == "PENDING" and pending.event is None
        assert len(render_calls) == 0
        assert not invocation_rows(first, "ProductionDispatchAttempt.v1")
        recovered, = w2_pulse_case["make_owner"](state_dir=state_dir).resume_local()
        assert recovered.status == "SUCCESS" and recovered.receiver_receipt["state"] == "WAIT_BRAIN"
        assert len(render_calls) == 1
        return

    real_invoke = first._invoke_pulse
    if window == "after-reservation":
        def crash_after_invoke(completed, raw):
            witness = real_invoke(completed, raw)
            raise LocalCrash()
        monkeypatch.setattr(first, "_invoke_pulse", crash_after_invoke)
    else:
        monkeypatch.setattr(first, "_finish_local", lambda *args: (_ for _ in ()).throw(LocalCrash()))

    with pytest.raises(LocalCrash):
        first.complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    assert not invocation_rows(first, "ProductionStageReceipt.v1")
    assert len(render_calls) == 1

    # Advance clock past lease expiry (60s) while keeping local rights (600s) fresh.
    import time
    expired_lease_now = time.time() + 120
    monkeypatch.setattr("ai_commerce.production_orchestration.orchestrator.time.time", lambda: expired_lease_now)
    second = w2_pulse_case["make_owner"](state_dir=state_dir)
    recovered, = second.resume_local()
    assert recovered.status == ("UNKNOWN" if window == "after-reservation" else "SUCCESS")
    assert second.resume_local() == (recovered,)
    assert len(render_calls) == 1
    assert len(invocation_rows(first, "ProductionStageReceipt.v1")) == (window == "after-witness")


def test_w2_pulse02_negative_corrupt_stale_rights_q4_witness_and_authority(w2_pulse_case, monkeypatch):
    _w2_require_windows_media()
    from ai_commerce.production_runtime import attended_worker
    from ai_commerce.production_runtime.contracts import (
        canonical_bytes as pulse_canonical_bytes,
        seal_contract,
    )

    # 1) Stale source SHA, missing/revoked auth, and wrong route fail closed with no state created.
    with pytest.raises(OrchestrationError, match="SOURCE_SHA_MISMATCH"):
        w2_pulse_case["make_owner"](
            state_dir=w2_pulse_case["tmp_path"] / "neg-sha", source_sha="0" * 40
        ).complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    with pytest.raises(OrchestrationError, match="LOCAL_AUTHORIZATION_REQUIRED"):
        w2_pulse_case["make_owner"](
            state_dir=w2_pulse_case["tmp_path"] / "neg-noauth", local_authorization=None
        ).complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    wrong_route_grant = replace(w2_pulse_case["grant"], route_id="a03-to-a04")
    with pytest.raises(OrchestrationError, match="LOCAL_ROUTE_MISMATCH"):
        w2_pulse_case["make_owner"](
            state_dir=w2_pulse_case["tmp_path"] / "neg-route", local_authorization=wrong_route_grant
        ).complete_a04_pulse(**w2_pulse_case["call_kwargs"])

    # 2) Expired rights fail closed before enqueueing or calling PULSE.
    exp_claim = {**w2_pulse_case["rights_claim"], "expires_at": 1}
    exp_raw = pulse_canonical_bytes(exp_claim)
    exp_path = w2_pulse_case["asset_dir"] / "rights-expired.json"
    exp_path.write_bytes(exp_raw)
    exp_sha = bytes_digest(exp_raw)
    exp_ref = "rights:" + exp_sha
    exp_dir_body = copy.deepcopy(w2_pulse_case["directive_body"])
    exp_dir_body["assets"][0]["rights"] = {"status": "GRANTED", "ref": exp_ref, "sha256": exp_sha}
    exp_dir = seal_contract("CreativeDirective", exp_dir_body)
    exp_dir_path = w2_pulse_case["tmp_path"] / "directive-expired.json"
    exp_dir_path.write_bytes(exp_dir.raw)
    neg_exp_dir = w2_pulse_case["tmp_path"] / "neg-expired"
    with pytest.raises(OrchestrationError, match="PRODUCER_CONTRACT_INVALID"):
        w2_pulse_case["make_owner"](state_dir=neg_exp_dir).complete_a04_pulse(**{
            **w2_pulse_case["call_kwargs"],
            "directive_ref": exp_dir_path,
            "rights": (f"{exp_ref}={exp_path}",),
        })
    assert not neg_exp_dir.exists()

    # 3) Corrupted PNG bytes after outbox commit fail closed as terminal FAILED without calling PULSE.
    corrupt_dir = w2_pulse_case["tmp_path"] / "neg-corrupt-png"
    corrupt_owner = w2_pulse_case["make_owner"](state_dir=corrupt_dir)
    corrupt_owner.complete_a04_pulse(**w2_pulse_case["call_kwargs"], dispatch=False)
    saved_png = w2_pulse_case["png_path"].read_bytes()
    try:
        w2_pulse_case["png_path"].write_bytes(_w2_png_bytes(220))
        failed, = w2_pulse_case["make_owner"](state_dir=corrupt_dir).resume_local()
        assert failed.status == "FAILED" and failed.reason == "INPUT_REJECTED" and failed.event is None
        assert not invocation_rows(corrupt_owner, "ProductionDispatchAttempt.v1")
    finally:
        w2_pulse_case["png_path"].write_bytes(saved_png)

    # 4) Pre-invocation missing directive has bounded RETRY_SAFE and converges once restored.
    retry_dir = w2_pulse_case["tmp_path"] / "neg-missing-retry"
    retry_owner = w2_pulse_case["make_owner"](state_dir=retry_dir, max_attempts=2)
    retry_owner.complete_a04_pulse(**w2_pulse_case["call_kwargs"], dispatch=False)
    saved_dir_bytes = w2_pulse_case["directive_path"].read_bytes()
    w2_pulse_case["directive_path"].unlink()
    try:
        retry_res, = retry_owner.resume_local()
        assert retry_res.status == "RETRY_SAFE" and retry_res.reason == "INPUT_UNAVAILABLE"
        assert not invocation_rows(retry_owner, "ProductionDispatchAttempt.v1")
    finally:
        w2_pulse_case["directive_path"].write_bytes(saved_dir_bytes)
    done_res, = w2_pulse_case["make_owner"](state_dir=retry_dir, max_attempts=2).resume_local()
    assert done_res.status == "SUCCESS"

    # 5) Tampered receiver ProductJourneyReceipt.v1 before ACK fails witness readback closed.
    tamper_dir = w2_pulse_case["tmp_path"] / "neg-tamper-receipt"
    tamper_owner = w2_pulse_case["make_owner"](state_dir=tamper_dir)
    monkeypatch.setattr(tamper_owner, "_finish_local", lambda *args: (_ for _ in ()).throw(LocalCrash()))
    with pytest.raises(LocalCrash):
        tamper_owner.complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    witness = invocation_rows(tamper_owner, "A04PulseLocalInvocation.v1")[0]
    Path(witness["artifact_ref"]).write_bytes(b'{"tampered":true}')
    with pytest.raises(OrchestrationError, match="INVOCATION_WITNESS_MISMATCH"):
        w2_pulse_case["make_owner"](state_dir=tamper_dir).resume_local()
    assert not invocation_rows(tamper_owner, "ProductionStageReceipt.v1")

    # 6) Post-reservation receiver failure / invalid Q4 transitions to UNKNOWN and never mints SUCCESS.
    q4_dir = w2_pulse_case["tmp_path"] / "neg-q4-unknown"
    calls = []
    def broken_receive(**kwargs):
        calls.append(kwargs["packet_path"])
        raise attended_worker.WorkerHold("Q4_TECHNICAL_QA_FAILED")
    monkeypatch.setattr(attended_worker, "receive_a04_pulse_handoff", broken_receive)
    unknown = w2_pulse_case["make_owner"](state_dir=q4_dir).complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    assert unknown.status == "UNKNOWN" and unknown.reason == "RECEIVER_UNRESOLVED" and unknown.event is None
    assert w2_pulse_case["make_owner"](state_dir=q4_dir).resume_local() == (unknown,)
    assert len(calls) == 1
    assert not invocation_rows(w2_pulse_case["make_owner"](state_dir=q4_dir), "ProductionStageReceipt.v1")


def _w3a_make_test_only_decision_and_authority(
    req_data: dict,
    *,
    now_at: int,
    decision: str = "APPROVE_DIRECTIVE",
    decision_id: str = "decision:w3a-test-01",
    chat_ref: str = "chat:w3a-current-chat",
    context_chat_ref: str | None = None,
    actor: str = "CHATGPT_BRAIN",
    actor_ref: str = "brain:chatgpt-current",
    proof_actor_ref: str | None = None,
    evidence_type: str = "HUMAN_SUPPLIED",
    proof_evidence_type: str = "HUMAN_SUPPLIED",
    channel: str = "CURRENT_CHATGPT_CHAT",
    proof_channel: str = "CURRENT_CHATGPT_CHAT",
    receipt_ref: str | None = None,
    source_sha: str | None = None,
    journey_id: str | None = None,
    directive_version: int | None = None,
    directive_sha256: str | None = None,
    scope: dict | None = None,
    valid_from: int | None = None,
    expires_at: int | None = None,
    verified: bool = True,
    revoked: bool = False,
    revocation_checked_at: int | None = None,
    verifier_ref: str = "TEST_ONLY:synthetic-private-verifier",
    proof_verifier_ref: str | None = None,
    transport_ref: str = "TEST_ONLY:synthetic-brain-transport",
    evidence_request_sha256: str | None = None,
    receipt_sha256: str | None = None,
    receipt_file_sha256: str | None = None,
    artifact_sha256: str | None = None,
    q4_qa_sha256: str | None = None,
    journal_checkpoint: dict | None = None,
    decision_bytes_sha256: str | None = None,
):
    from ai_commerce.production_orchestration.contracts import BrainAuthorityVerification
    from ai_commerce.production_runtime.contracts import draft_authorization, seal_contract
    from ai_commerce.production_runtime.state_machine import (
        AuthorityKind,
        AuthorityReceipt,
        VerificationContext,
    )

    eff_source = req_data["source_sha"] if source_sha is None else source_sha
    eff_journey = req_data["journey_id"] if journey_id is None else journey_id
    eff_dir_ver = req_data["directive_version"] if directive_version is None else directive_version
    eff_dir_sha = req_data["directive_sha256"] if directive_sha256 is None else directive_sha256
    eff_scope = copy.deepcopy(req_data["scope"]) if scope is None else scope
    eff_receipt_ref = req_data["receipt_ref"] if receipt_ref is None else receipt_ref
    body = {
        "schema_version": "BrainDecision.v1",
        "decision_id": decision_id,
        "journey_id": eff_journey,
        "source_sha": eff_source,
        "directive_version": eff_dir_ver,
        "directive_sha256": eff_dir_sha,
        "scope": eff_scope,
        "decision": decision,
        "review_scope": "DIRECTIVE_ONLY",
        "channel": channel,
        "chat_ref": chat_ref,
        "receipt_ref": eff_receipt_ref,
        "rationale": "TEST_ONLY synthetic fixture decision, never live private Brain authority.",
        "provenance": {
            "actor": actor,
            "actor_ref": actor_ref,
            "evidence_type": evidence_type,
            "source_ref": eff_receipt_ref,
            "source_sha": eff_source,
        },
        "authorization": draft_authorization(),
    }
    sealed = seal_contract("BrainDecision", body)
    decision_dict = sealed.to_dict()
    ckpt = req_data["journal_checkpoint"] if journal_checkpoint is None else journal_checkpoint
    proof = AuthorityReceipt(
        kind=AuthorityKind.BRAIN,
        journey_id=eff_journey,
        source_sha=eff_source,
        directive_sha256=eff_dir_sha,
        scope_sha256=req_data["scope_sha256"],
        record_sha256=decision_dict["decision_sha256"],
        actor_ref=actor_ref if proof_actor_ref is None else proof_actor_ref,
        channel=proof_channel,
        actions=("APPROVE_DIRECTIVE",),
        verifier_ref=verifier_ref if proof_verifier_ref is None else proof_verifier_ref,
        valid_from=(now_at - 60) if valid_from is None else valid_from,
        expires_at=(now_at + 300) if expires_at is None else expires_at,
        evidence_type=proof_evidence_type,
        verified=verified,
    )
    ctx = VerificationContext(
        receipts=(proof,),
        current_chat_ref=chat_ref if context_chat_ref is None else context_chat_ref,
    )
    authority = BrainAuthorityVerification(
        verification_context=ctx,
        verifier_ref=verifier_ref,
        transport_ref=transport_ref,
        authority_class="TEST_ONLY_SYNTHETIC_VERIFIER",
        evidence_request_sha256=req_data["request_sha256"] if evidence_request_sha256 is None else evidence_request_sha256,
        receipt_sha256=req_data["receipt_sha256"] if receipt_sha256 is None else receipt_sha256,
        receipt_file_sha256=req_data["receipt_file_sha256"] if receipt_file_sha256 is None else receipt_file_sha256,
        artifact_sha256=req_data["local_artifact"]["artifact_sha256"] if artifact_sha256 is None else artifact_sha256,
        q4_qa_sha256=req_data["q4_technical_qa"]["qa_sha256"] if q4_qa_sha256 is None else q4_qa_sha256,
        journal_id=ckpt["journal_id"],
        journal_position=ckpt["position"],
        journal_checkpoint_sha256=ckpt["sha256"],
        journal_generation=ckpt["generation"],
        decision_bytes_sha256=bytes_digest(sealed.raw) if decision_bytes_sha256 is None else decision_bytes_sha256,
        revoked=revoked,
        revocation_checked_at=now_at if revocation_checked_at is None else revocation_checked_at,
        revocation_ref="TEST_ONLY:revocation-check-01",
    )
    return sealed.raw, decision_dict, authority


def test_w3a_pulse03_evidence_request_and_absent_or_forged_transport_preserve_wait_brain(w2_pulse_case, monkeypatch):
    _w2_require_windows_media()
    import time
    from ai_commerce.production_runtime import attended_worker, receipt as runtime_receipt

    owner = w2_pulse_case["make_owner"]()
    done = owner.complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    assert done.status == "SUCCESS" and done.receiver_receipt["state"] == "WAIT_BRAIN"
    key = done.completed.spec.idempotency_key

    monkeypatch.setattr(attended_worker.service, "run", lambda *a, **k: pytest.fail("W3a must never render"))
    req_raw = owner.build_pulse_brain_evidence_request(key)
    req_data = runtime_receipt.parse_brain_review_evidence_request(req_raw)
    assert owner.build_pulse_brain_evidence_request(key) == req_raw
    assert str(w2_pulse_case["tmp_path"]).encode("utf-8") not in req_raw
    assert b"Silent offline visual showcase" not in req_raw
    assert req_data["receipt_sha256"] == done.receiver_receipt["receipt_sha256"]
    assert req_data["local_artifact"]["artifact_sha256"] == done.receiver_receipt["artifact_sha256"]
    assert req_data["q4_technical_qa"]["qa_sha256"] == done.receiver_receipt["q4_qa_sha256"]
    assert req_data["journal_checkpoint"] == done.receiver_receipt["journal_checkpoint"]

    now_at = int(time.time())
    decision_raw, _, synthetic_auth = _w3a_make_test_only_decision_and_authority(req_data, now_at=now_at)

    # Structurally valid BrainDecision.v1 JSON alone without private verifier preserves WAIT_BRAIN.
    absent = owner.accept_pulse_brain_decision(
        key,
        decision_bytes=decision_raw,
        authority=None,
        evidence_request=req_raw,
        at=now_at,
    )
    assert absent.status == "AUTHENTICATED_BRAIN_TRANSPORT_UNAVAILABLE"
    assert absent.state == "WAIT_BRAIN"
    assert absent.accepted is False
    assert absent.live_private_authority_proven is False
    assert absent.rights_granted is False and absent.provider_grant is False
    assert absent.publish_authorized is False and absent.spend_authorized is False

    # GitHub PR Brain PASS comment, model text, dict, or bare VerificationContext never attest Brain.
    for forged_authority in (
        "AI_COMMERCE_BRAIN_REVIEW_PASS_V1",
        {"verified": True, "actor": "CHATGPT_BRAIN", "chat_ref": "chat:w3a-current-chat"},
        synthetic_auth.verification_context,
    ):
        held = owner.accept_pulse_brain_decision(
            key,
            decision_bytes=decision_raw,
            authority=forged_authority,
            evidence_request=req_raw,
            at=now_at,
        )
        assert held.status == "HOLD"
        assert held.reason == "FORGED_OR_UNTYPED_BRAIN_AUTHORITY"
        assert held.state == "WAIT_BRAIN"
        assert held.accepted is False

    assert not invocation_rows(owner, "BrainDecisionIntakeReserved.v1")
    assert not invocation_rows(owner, "BrainDecisionAccepted.v1")
    assert owner.inspect_pulse_brain_decision(key).state == "WAIT_BRAIN"


def test_w3a_pulse03_test_only_verified_context_accepts_once_and_reopens_idempotently(w2_pulse_case, monkeypatch):
    _w2_require_windows_media()
    import time
    from ai_commerce.production_runtime import attended_worker, receipt as runtime_receipt
    from ai_commerce.production_runtime.contracts import draft_authorization, parse_contract

    owner = w2_pulse_case["make_owner"]()
    done = owner.complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    key = done.completed.spec.idempotency_key
    monkeypatch.setattr(attended_worker.service, "run", lambda *a, **k: pytest.fail("W3a must never render"))

    req_raw = owner.build_pulse_brain_evidence_request(key)
    req_data = runtime_receipt.parse_brain_review_evidence_request(req_raw)
    now_at = int(time.time())
    decision_raw, decision_dict, authority = _w3a_make_test_only_decision_and_authority(req_data, now_at=now_at)

    accepted = owner.accept_pulse_brain_decision(
        key,
        decision_bytes=decision_raw,
        authority=authority,
        evidence_request=req_raw,
        at=now_at,
    )
    assert accepted.status == "BRAIN_ACCEPTED"
    assert accepted.state == "READY_FOR_HUMAN_RELEASE"
    assert accepted.accepted is True
    assert accepted.idempotent_replay is False
    assert accepted.authority_class == "TEST_ONLY_SYNTHETIC_VERIFIER"
    assert accepted.live_private_authority_proven is False
    assert accepted.human_attended_required is False
    assert accepted.rights_granted is False and accepted.provider_grant is False
    assert accepted.publish_authorized is False and accepted.spend_authorized is False
    assert accepted.network_calls_performed == accepted.publish_calls == accepted.paid_generation_calls == 0
    assert accepted.decision_id == decision_dict["decision_id"]
    assert accepted.decision_sha256 == decision_dict["decision_sha256"]
    assert accepted.decision_bytes_sha256 == bytes_digest(decision_raw)

    # Verify persisted B0.1 ProductJourneyReceipt.v1 at READY_FOR_HUMAN_RELEASE.
    receipt_path = Path(accepted.accepted_receipt_ref)
    persisted_receipt = parse_contract("ProductJourneyReceipt", receipt_path.read_bytes()).to_dict()
    assert persisted_receipt["state"] == "READY_FOR_HUMAN_RELEASE"
    assert persisted_receipt["brain_decision"] == {
        "ref": decision_dict["decision_id"],
        "sha256": decision_dict["decision_sha256"],
    }
    assert persisted_receipt["effect"]["status"] == "NONE"
    assert persisted_receipt["authorization"] == draft_authorization()
    assert persisted_receipt["network_calls_performed"] is False
    assert persisted_receipt["external_effects_performed"] is False

    # Reopen orchestrator and confirm exact single logical decision + idempotent replay.
    reopened = w2_pulse_case["make_owner"]()
    inspected = reopened.inspect_pulse_brain_decision(key)
    assert inspected.status == "BRAIN_ACCEPTED"
    assert inspected.state == "READY_FOR_HUMAN_RELEASE"
    assert inspected.journal_seq == accepted.journal_seq
    assert inspected.decision_sha256 == accepted.decision_sha256
    assert inspected.live_private_authority_proven is False

    replayed = reopened.accept_pulse_brain_decision(
        key,
        decision_bytes=decision_raw,
        authority=authority,
        evidence_request=req_raw,
        at=now_at,
    )
    assert replayed.status == "BRAIN_ACCEPTED"
    assert replayed.idempotent_replay is True
    assert replayed.journal_seq == accepted.journal_seq
    assert replayed.accepted_receipt_sha256 == accepted.accepted_receipt_sha256

    # Conflicting second decision on the same logical journey is rejected; ONE persistent logical decision remains.
    second_raw, _, second_auth = _w3a_make_test_only_decision_and_authority(
        req_data,
        now_at=now_at,
        decision_id="decision:w3a-conflicting-second",
    )
    conflict = reopened.accept_pulse_brain_decision(
        key,
        decision_bytes=second_raw,
        authority=second_auth,
        evidence_request=req_raw,
        at=now_at,
    )
    assert conflict.status == "HOLD" and conflict.reason == "BRAIN_DECISION_CONFLICT" and conflict.accepted is False
    assert len(invocation_rows(owner, "BrainDecisionIntakeReserved.v1")) == 1
    assert len(invocation_rows(owner, "BrainDecisionAccepted.v1")) == 1
    # W2 WAIT_BRAIN outbox/receipt and user_version=1 remain intact.
    assert reopened.resume_local() == (done,)
    with sqlite3.connect(owner._state_dir / "production.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_w3a_pulse03_negative_wrong_chat_stale_revoked_mismatched_and_tampered(w2_pulse_case, monkeypatch):
    _w2_require_windows_media()
    import time
    from ai_commerce.production_runtime import attended_worker, receipt as runtime_receipt

    owner = w2_pulse_case["make_owner"]()
    done = owner.complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    key = done.completed.spec.idempotency_key
    req_raw = owner.build_pulse_brain_evidence_request(key)
    req_data = runtime_receipt.parse_brain_review_evidence_request(req_raw)
    now_at = int(time.time())

    cases = [
        ("wrong-chat", {"context_chat_ref": "chat:wrong-other-chat"}, "BRAIN_CHAT_MISMATCH"),
        ("fake-actor-human", {"actor": "HUMAN"}, "BRAIN_ACTOR_MISMATCH"),
        ("wrong-actor-ref", {"proof_actor_ref": "brain:other-actor"}, "BRAIN_ACTOR_MISMATCH"),
        ("decision-revise", {"decision": "REVISE"}, "BRAIN_DECISION_NOT_APPROVED"),
        ("decision-hold", {"decision": "HOLD"}, "BRAIN_DECISION_NOT_APPROVED"),
        ("decision-stop", {"decision": "STOP"}, "BRAIN_DECISION_NOT_APPROVED"),
        ("expired", {"valid_from": now_at - 100, "expires_at": now_at}, "BRAIN_AUTHORITY_EXPIRED"),
        ("not-yet-valid", {"valid_from": now_at + 10, "expires_at": now_at + 100}, "BRAIN_AUTHORITY_EXPIRED"),
        ("revoked", {"revoked": True}, "BRAIN_AUTHORITY_REVOKED"),
        ("stale-revocation", {"revocation_checked_at": now_at - 301}, "BRAIN_AUTHORITY_REVOCATION_STALE"),
        ("unverified", {"verified": False}, "BRAIN_AUTHORITY_UNVERIFIED"),
        ("fixture-proof", {"proof_evidence_type": "SYNTHETIC_FIXTURE"}, "BRAIN_AUTHORITY_UNVERIFIED"),
        ("wrong-verifier", {"proof_verifier_ref": "TEST_ONLY:other-verifier"}, "BRAIN_VERIFIER_MISMATCH"),
        ("wrong-channel", {"proof_channel": "PUBLIC_GITHUB_PR"}, "BRAIN_CHANNEL_MISMATCH"),
        ("wrong-source", {"source_sha": "0" * 40}, "BRAIN_SOURCE_MISMATCH"),
        ("wrong-journey", {"journey_id": "journey:" + "9" * 32}, "BRAIN_JOURNEY_MISMATCH"),
        ("wrong-version", {"directive_version": 2}, "BRAIN_VERSION_MISMATCH"),
        ("wrong-directive", {"directive_sha256": "0" * 64}, "BRAIN_DIRECTIVE_MISMATCH"),
        ("wrong-scope", {"scope": {"action_class": "PUBLISH", "provider_ref": "provider:x",
                                   "account_ref": "account:x", "surface": "surface:x", "asset_sha256": "a" * 64}},
         "BRAIN_SCOPE_MISMATCH"),
        ("wrong-receipt-ref", {"receipt_ref": "receipt:" + "0" * 64}, "BRAIN_RECEIPT_MISMATCH"),
        ("wrong-receipt-sha", {"receipt_sha256": "0" * 64}, "BRAIN_RECEIPT_MISMATCH"),
        ("wrong-artifact-sha", {"artifact_sha256": "0" * 64}, "BRAIN_ARTIFACT_MISMATCH"),
        ("wrong-q4-sha", {"q4_qa_sha256": "0" * 64}, "BRAIN_QA_MISMATCH"),
        ("wrong-request-sha", {"evidence_request_sha256": "0" * 64}, "BRAIN_EVIDENCE_REQUEST_MISMATCH"),
        ("wrong-decision-bytes-sha", {"decision_bytes_sha256": "0" * 64}, "BRAIN_DECISION_BYTES_MISMATCH"),
        ("wrong-checkpoint", {"journal_checkpoint": {**req_data["journal_checkpoint"], "sha256": "0" * 64}},
         "BRAIN_JOURNAL_CHECKPOINT_MISMATCH"),
    ]
    for label, overrides, expected_reason in cases:
        dec_raw, _, auth = _w3a_make_test_only_decision_and_authority(req_data, now_at=now_at, **overrides)
        res = owner.accept_pulse_brain_decision(
            key,
            decision_bytes=dec_raw,
            authority=auth,
            evidence_request=req_raw,
            at=now_at,
        )
        assert res.status == "HOLD" and res.accepted is False and res.state == "WAIT_BRAIN", (label, res)
        assert res.reason == expected_reason, (label, res.reason, expected_reason)

    # Changed raw bytes (trailing newline) and malformed JSON fail closed.
    valid_raw, _, valid_auth = _w3a_make_test_only_decision_and_authority(req_data, now_at=now_at)
    changed_bytes_res = owner.accept_pulse_brain_decision(
        key,
        decision_bytes=valid_raw + b"\n",
        authority=valid_auth,
        evidence_request=req_raw,
        at=now_at,
    )
    assert changed_bytes_res.status == "HOLD" and changed_bytes_res.reason == "BRAIN_DECISION_BYTES_MISMATCH"
    bad_json_res = owner.accept_pulse_brain_decision(
        key,
        decision_bytes=b"{not-json",
        authority=valid_auth,
        evidence_request=req_raw,
        at=now_at,
    )
    assert bad_json_res.status == "HOLD" and bad_json_res.reason == "BRAIN_DECISION_INVALID"
    bad_req_res = owner.accept_pulse_brain_decision(
        key,
        decision_bytes=valid_raw,
        authority=valid_auth,
        evidence_request=req_raw + b" ",
        at=now_at,
    )
    assert bad_req_res.status == "HOLD" and bad_req_res.reason == "BRAIN_EVIDENCE_REQUEST_MISMATCH"

    # Tampered production.sqlite brain journal row is rejected fail-closed.
    tamper_dir = w2_pulse_case["tmp_path"] / "w3a-tamper-journal"
    tamper_owner = w2_pulse_case["make_owner"](state_dir=tamper_dir)
    tamper_done = tamper_owner.complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    t_key = tamper_done.completed.spec.idempotency_key
    t_req_raw = tamper_owner.build_pulse_brain_evidence_request(t_key)
    t_req = runtime_receipt.parse_brain_review_evidence_request(t_req_raw)
    t_dec_raw, _, t_auth = _w3a_make_test_only_decision_and_authority(t_req, now_at=now_at)
    with sqlite3.connect(tamper_dir / "production.sqlite") as db:
        db.execute(
            "INSERT INTO journal(key,kind,body) VALUES (?,?,?)",
            (t_key, "BrainDecisionAccepted.v1", '{"tampered":true}'),
        )
    tampered_res = tamper_owner.accept_pulse_brain_decision(
        t_key,
        decision_bytes=t_dec_raw,
        authority=t_auth,
        evidence_request=t_req_raw,
        at=now_at,
    )
    assert tampered_res.status == "HOLD" and tampered_res.reason == "BRAIN_JOURNAL_TAMPERED" and tampered_res.accepted is False

    # Tampered B0.2 journal.sqlite fails closed without minting ACK.
    b02_dir = w2_pulse_case["tmp_path"] / "w3a-tamper-b02"
    b02_owner = w2_pulse_case["make_owner"](state_dir=b02_dir)
    b02_done = b02_owner.complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    b_key = b02_done.completed.spec.idempotency_key
    b_req_raw = b02_owner.build_pulse_brain_evidence_request(b_key)
    b_req = runtime_receipt.parse_brain_review_evidence_request(b_req_raw)
    b_dec_raw, _, b_auth = _w3a_make_test_only_decision_and_authority(b_req, now_at=now_at)
    private_dir = b02_dir / "pulse-runtime" / b_key[:32]
    (private_dir / "journal.sqlite").write_bytes(b"corrupt pulse journal")
    b02_res = b02_owner.accept_pulse_brain_decision(
        b_key,
        decision_bytes=b_dec_raw,
        authority=b_auth,
        evidence_request=b_req_raw,
        at=now_at,
    )
    assert b02_res.status == "HOLD" and b02_res.reason == "BRAIN_JOURNAL_OR_SOURCE_TAMPERED" and b02_res.accepted is False
    assert not invocation_rows(owner, "BrainDecisionAccepted.v1")

    # UNKNOWN_EFFECT on underlying PULSE journal fails closed and preserves UNKNOWN_EFFECT.
    with monkeypatch.context() as mp:
        mp.setattr(
            attended_worker,
            "load_a04_pulse_wait_brain_snapshot",
            lambda **kw: (_ for _ in ()).throw(attended_worker.WorkerHold("UNKNOWN_EFFECT")),
        )
        unk_res = owner.accept_pulse_brain_decision(
            key,
            decision_bytes=valid_raw,
            authority=valid_auth,
            evidence_request=req_raw,
            at=now_at,
        )
    assert (
        unk_res.status == "HOLD"
        and unk_res.state == "UNKNOWN_EFFECT"
        and unk_res.reason == "BRAIN_UNKNOWN_EFFECT"
        and unk_res.accepted is False
    )

    # Revoked caller authorization cannot intake BrainDecision.
    owner.revoke_local_authorization()
    with pytest.raises(OrchestrationError, match="LOCAL_AUTHORIZATION_REQUIRED"):
        owner.accept_pulse_brain_decision(
            key,
            decision_bytes=valid_raw,
            authority=valid_auth,
            evidence_request=req_raw,
            at=now_at,
        )


@pytest.mark.parametrize("cut", ["after_reserve", "after_receipt_write"])
def test_w3a_pulse03_crash_fencing_and_interrupted_commit_never_double_accepts(w2_pulse_case, monkeypatch, cut):
    _w2_require_windows_media()
    import time
    from ai_commerce.production_runtime import receipt as runtime_receipt

    state_dir = w2_pulse_case["tmp_path"] / f"w3a-crash-{cut}"
    first = w2_pulse_case["make_owner"](state_dir=state_dir)
    done = first.complete_a04_pulse(**w2_pulse_case["call_kwargs"])
    key = done.completed.spec.idempotency_key
    req_raw = first.build_pulse_brain_evidence_request(key)
    req_data = runtime_receipt.parse_brain_review_evidence_request(req_raw)
    now_at = int(time.time())
    decision_raw, _, authority = _w3a_make_test_only_decision_and_authority(req_data, now_at=now_at)

    # Stale expected_fencing_generation is rejected before writing any reservation.
    stale = first.accept_pulse_brain_decision(
        key,
        decision_bytes=decision_raw,
        authority=authority,
        evidence_request=req_raw,
        at=now_at,
        expected_fencing_generation=999,
    )
    assert stale.status == "HOLD" and stale.reason == "STALE_FENCING_GENERATION" and stale.accepted is False
    assert not invocation_rows(first, "BrainDecisionIntakeReserved.v1")

    def crash_at_cut(name):
        if name == cut:
            raise LocalCrash()

    monkeypatch.setattr(first, "_brain_decision_cut", crash_at_cut)
    with pytest.raises(LocalCrash):
        first.accept_pulse_brain_decision(
            key,
            decision_bytes=decision_raw,
            authority=authority,
            evidence_request=req_raw,
            at=now_at,
            expected_fencing_generation=req_data["journal_checkpoint"]["generation"],
        )
    assert len(invocation_rows(first, "BrainDecisionIntakeReserved.v1")) == 1
    assert not invocation_rows(first, "BrainDecisionAccepted.v1")

    # While reservation lease is active, a concurrent caller holds without mutating.
    second = w2_pulse_case["make_owner"](state_dir=state_dir)
    busy = second.accept_pulse_brain_decision(
        key,
        decision_bytes=decision_raw,
        authority=authority,
        evidence_request=req_raw,
        at=now_at,
    )
    assert busy.status == "HOLD" and busy.reason == "BRAIN_DECISION_INTAKE_IN_PROGRESS" and busy.accepted is False

    # After lease expiry, interrupted commit holds without explicit exact-generation reconciliation.
    expired_now = time.time() + 120
    monkeypatch.setattr("ai_commerce.production_orchestration.orchestrator.time.time", lambda: expired_now)
    held = second.accept_pulse_brain_decision(
        key,
        decision_bytes=decision_raw,
        authority=authority,
        evidence_request=req_raw,
        at=now_at,
    )
    assert held.status == "HOLD" and held.reason == "BRAIN_INTERRUPTED_COMMIT_HOLD" and held.accepted is False

    # Wrong reconcile_fencing_generation is denied.
    wrong_fence = second.accept_pulse_brain_decision(
        key,
        decision_bytes=decision_raw,
        authority=authority,
        evidence_request=req_raw,
        at=now_at,
        reconcile_fencing_generation=999,
    )
    assert wrong_fence.status == "HOLD" and wrong_fence.reason == "STALE_FENCING_GENERATION"

    # Reconciling with exact generation:
    # - after_reserve has no persisted receipt files -> stays HOLD (never mints ACK from incomplete commit).
    # - after_receipt_write has exact persisted receipt files -> converges cleanly to ONE BrainDecisionAccepted.v1.
    reconciled = second.accept_pulse_brain_decision(
        key,
        decision_bytes=decision_raw,
        authority=authority,
        evidence_request=req_raw,
        at=now_at,
        reconcile_fencing_generation=req_data["journal_checkpoint"]["generation"],
    )
    if cut == "after_reserve":
        assert reconciled.status == "HOLD" and reconciled.reason == "BRAIN_INTERRUPTED_COMMIT_HOLD"
        assert not invocation_rows(second, "BrainDecisionAccepted.v1")
    else:
        assert reconciled.status == "BRAIN_ACCEPTED" and reconciled.state == "READY_FOR_HUMAN_RELEASE"
        assert len(invocation_rows(second, "BrainDecisionIntakeReserved.v1")) == 1
        assert len(invocation_rows(second, "BrainDecisionAccepted.v1")) == 1
        assert second.inspect_pulse_brain_decision(key).journal_seq == reconciled.journal_seq
