"""Offline contract/owner evidence; no live dispatch or publication proof."""
import copy
from dataclasses import FrozenInstanceError, replace
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import sqlite3

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


@pytest.mark.parametrize(("provider", "failure"), [
    ("antigravity-cli", "AGY_AUTHENTICATED_CLI_SESSION_UNAVAILABLE"),
    ("chatgpt-brain-cdp", "BRAIN_CDP_ORIGIN_AND_TURN_AUTHORITY_UNAVAILABLE"),
])
def test_c1_unavailable_r3_provider_cannot_publish_a04_handoff(a03_r3_case, monkeypatch, provider, failure):
    from ai_commerce.creative import ModelConfiguration, run_creative_production
    from ai_commerce.media import generation as mg

    monkeypatch.setattr(mg, "accept_creative_production_package", lambda **_: pytest.fail("A04 invoked"))
    sidecars = a03_r3_case["sidecars"]
    owner = a03_r3_case["make_owner"]()
    job = replace(a03_r3_case["job"], attempt=2, configuration=ModelConfiguration(provider))
    grant = a03_r3_case["cli_grant"]
    # The existing Codex client must never be tried as a fallback.
    reasoner = a03_r3_case["reasoner"]
    result = run_creative_production(job, sidecars, reasoner, authorization=grant, production=owner)
    assert (result.status, result.failure, result.package, result.stage) == ("HOLD", failure, None, None)
    assert run_creative_production(job, sidecars, reasoner, authorization=grant, production=owner) == result
    assert a03_r3_case["cli"].calls == [] and owner.last_result is None
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
