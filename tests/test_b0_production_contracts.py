"""B0.1 design proofs only. Verified contexts below are hypothetical test inputs.

No test authenticates ChatGPT, a Human, a provider or an external effect.
"""
from __future__ import annotations

import ast
import builtins
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import http.client
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import urllib.request

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.production_runtime import (
    AuthorityKind, AuthorityReceipt, ContractError, Event, EventKind, EvidencePin,
    Journey, State, TransitionError, VerificationContext, canonical_bytes,
    draft_authorization, parse_contract, schema, seal_contract, transition, validate_contract,
)
from ai_commerce.production_runtime.contracts import NAMES, STATES

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "26398290f8c2440eaa383783dbdf84f8d95610ab"
ASSET = "a" * 64
RIGHTS = "b" * 64
QA = EvidencePin("MEDIA_CREATIVE", "fixture:qa", "c" * 64, "SYNTHETIC_FIXTURE")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("B0.1 attempted external access")
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(urllib.request, "urlopen", deny)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", deny)


def provenance(actor="HUMAN", evidence="HUMAN_SUPPLIED"):
    return {"actor": actor, "actor_ref": "brain:fixture" if actor == "CHATGPT_BRAIN" else "human:fixture",
            "evidence_type": evidence, "source_ref": "fixture:input", "source_sha": SOURCE}


def directive_body(publish=False):
    return {"schema_version": "CreativeDirective.v1", "journey_id": "fixture:journey", "source_sha": SOURCE,
        "directive_version": 1, "creative": {"freeform": "Hoạt hình tự do; không cần SKU hay giọng nói.", "product_ref": None},
        "scope": {"action_class": "PUBLISH" if publish else "EXPORT_DRAFT",
            "provider_ref": "provider:fixture" if publish else None,
            "account_ref": "account:fixture" if publish else None,
            "surface": "surface:fixture" if publish else None, "asset_sha256": ASSET if publish else None},
        "assets": [{"ref": "asset:fixture", "sha256": ASSET, "evidence_type": "HUMAN_SUPPLIED",
                    "rights": {"status": "GRANTED", "ref": "rights:fixture", "sha256": RIGHTS}}],
        "action_classes": ["PLAN_OFFLINE", "PUBLISH" if publish else "EXPORT_DRAFT"],
        "allowed_tools": ["tool:offline", "tool:draft-adapter"],
        "provenance": provenance(), "authorization": draft_authorization()}


def directive(publish=False):
    return seal_contract("CreativeDirective", directive_body(publish))


def brain_body(directive_record):
    value = directive_record.to_dict()
    return {"schema_version": "BrainDecision.v1", "decision_id": "decision:fixture",
        **{key: value[key] for key in ("journey_id", "source_sha", "directive_version", "directive_sha256", "scope")},
        "decision": "APPROVE_DIRECTIVE", "review_scope": "DIRECTIVE_ONLY", "channel": "CURRENT_CHATGPT_CHAT",
        "chat_ref": "chat:fixture-current", "receipt_ref": "brain:attended-receipt",
        "rationale": "Hypothetical model input, never an actual Brain approval.",
        "provenance": provenance("CHATGPT_BRAIN"), "authorization": draft_authorization()}


def receipt_body():
    value = directive().to_dict()
    return {"schema_version": "ProductJourneyReceipt.v1",
        **{key: value[key] for key in ("journey_id", "source_sha", "directive_version", "directive_sha256", "scope")},
        "state": "WAIT_BRAIN", "brain_decision": None,
        "owner_evidence_refs": [{"owner": QA.owner, "ref": QA.ref, "sha256": QA.sha256,
                                "evidence_type": QA.evidence_type, "attestation_ref": None}],
        "effect": {"status": "NONE", "idempotency_key": None, "request_sha256": None, "receipt": None},
        "network_calls_performed": False, "external_effects_performed": False,
        "provenance": provenance(), "authorization": draft_authorization()}


def event(journey, kind, **kwargs):
    return Event("event:" + str(len(journey.seen_event_ids)), kind, journey.journey_id, journey.source_sha, **kwargs)


def step(journey, kind, verification=VerificationContext(), **kwargs):
    return transition(journey, event(journey, kind, **kwargs), at=10, verification=verification)


def waiting(publish=False):
    state = Journey("fixture:journey", SOURCE)
    state = step(state, EventKind.INPUT_ACCEPTED, record=directive(publish))
    state = step(state, EventKind.START, tool="tool:offline")
    state = step(state, EventKind.OUTPUT_READY, evidence=(QA,))
    return step(state, EventKind.QA_PASSED, evidence=(QA,))


def authority(journey, kind, record_digest, **kwargs):
    """Hypothetical output of a FUTURE verifier; this helper verifies nothing."""
    value = journey.directive.to_dict()
    channels = {AuthorityKind.BRAIN: "CURRENT_CHATGPT_CHAT", AuthorityKind.HUMAN_RELEASE: "HUMAN_ATTENDED"}
    actions = {AuthorityKind.BRAIN: ("APPROVE_DIRECTIVE",), AuthorityKind.HUMAN_RELEASE: ("GO_PUBLISH",),
        AuthorityKind.PROVIDER_ENTITLEMENT: ("PUBLISH",), AuthorityKind.ASSET_RIGHTS: ("ASSET_RIGHTS",),
        AuthorityKind.PROVIDER_OBSERVATION: ("OBSERVE_EFFECT",)}
    body = dict(kind=kind, journey_id=journey.journey_id, source_sha=SOURCE,
        directive_sha256=value["directive_sha256"], scope_sha256=sha256(canonical_bytes(value["scope"])).hexdigest(),
        record_sha256=record_digest, actor_ref="brain:fixture" if kind == AuthorityKind.BRAIN else "provider:fixture",
        channel=channels.get(kind, "PRIVATE_PROVIDER_VERIFIER"), actions=actions[kind],
        verifier_ref="hypothetical:private-verifier", valid_from=0, expires_at=100,
        evidence_type="HUMAN_SUPPLIED" if kind in {AuthorityKind.BRAIN, AuthorityKind.HUMAN_RELEASE, AuthorityKind.ASSET_RIGHTS}
                      else "PROVIDER_ATTESTED", verified=True)
    body.update(kwargs)
    return AuthorityReceipt(**body)


def ready(publish=False):
    state = waiting(publish)
    decision = seal_contract("BrainDecision", brain_body(state.directive))
    proof = authority(state, AuthorityKind.BRAIN, decision.to_dict()["decision_sha256"])
    context = VerificationContext((proof,), "chat:fixture-current")
    return step(state, EventKind.BRAIN_ACCEPTED, context, record=decision), context


def release_context(state, context):
    return replace(context, receipts=(*context.receipts,
        authority(state, AuthorityKind.HUMAN_RELEASE, "d" * 64),
        authority(state, AuthorityKind.PROVIDER_ENTITLEMENT, "e" * 64),
        authority(state, AuthorityKind.ASSET_RIGHTS, RIGHTS)))


def release(state, context, **kwargs):
    values = dict(tool="tool:draft-adapter", effect_key="effect:one",
                  human_grant_sha256="d" * 64, provider_entitlement_sha256="e" * 64)
    values.update(kwargs)
    return step(state, EventKind.RELEASE_REQUESTED, context, **values)


def pending():
    state, context = ready(True)
    context = release_context(state, context)
    return release(state, context), context


def observe(state, context, kind=EventKind.PROVIDER_CONFIRMED):
    pin = EvidencePin("DISTRIBUTION", "provider:receipt", "f" * 64, "PROVIDER_ATTESTED", "hypothetical:private-verifier")
    proof = authority(state, AuthorityKind.PROVIDER_OBSERVATION, pin.sha256,
        effect_key=state.effect_key, effect_request_sha256=state.effect_request_sha256,
        effect_outcome="CONFIRMED" if kind == EventKind.PROVIDER_CONFIRMED else "NO_EFFECT")
    extended = replace(context, receipts=(*context.receipts, proof))
    e = event(state, kind, evidence=(pin,), effect_key=state.effect_key,
              effect_request_sha256=state.effect_request_sha256)
    return e, extended


@pytest.mark.parametrize("name", NAMES)
def test_closed_exported_schemas_match_in_memory_and_draft_2020_12(name):
    exported = json.loads((ROOT / "contracts" / (name + ".v1.schema.json")).read_bytes())
    assert exported == schema(name)
    Draft202012Validator.check_schema(exported)
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            if "$ref" in node:
                assert node["$ref"].startswith("#/$defs/")
            for part in node.values():
                check(part)
        elif isinstance(node, list):
            for part in node:
                check(part)
    check(exported)


@pytest.mark.parametrize("name", NAMES)
def test_positive_roundtrip_immutable_and_detached(name):
    body = {"CreativeDirective": directive_body(), "BrainDecision": brain_body(directive()),
            "ProductJourneyReceipt": receipt_body()}[name]
    record = seal_contract(name, body)
    assert parse_contract(name, record.raw) == record
    assert validate_contract(name, record.to_dict()) == record
    assert Draft202012Validator(schema(name)).is_valid(record.to_dict())
    body["journey_id"] = "changed:input"
    assert record.to_dict()["journey_id"] == "fixture:journey"
    with pytest.raises(FrozenInstanceError):
        record.raw = b"{}"


def test_freeform_product_null_silence_no_asset_or_product_gate():
    body = directive_body()
    body["assets"] = []
    body["creative"]["freeform"] = "Hài hước, hư cấu, không lời; tự do sáng tạo."
    value = seal_contract("CreativeDirective", body).to_dict()
    assert value["creative"]["product_ref"] is None
    assert value["authorization"] == draft_authorization()


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("location", ["root", "scope", "provenance", "authorization"])
def test_unknown_fields_and_credential_fields_rejected(name, location):
    body = {"CreativeDirective": directive_body(), "BrainDecision": brain_body(directive()),
            "ProductJourneyReceipt": receipt_body()}[name]
    target = body if location == "root" else body[location]
    target["raw_cookie"] = "fixture-forbidden-field"
    with pytest.raises(ContractError, match="CONTRACT_SCHEMA_INVALID"):
        seal_contract(name, body)


@pytest.mark.parametrize("field,value", [("allow_network", True), ("allow_spend", True),
    ("allow_publish", True), ("provider_entitlement", True), ("spend_ceiling_minor", 1)])
@pytest.mark.parametrize("name", NAMES)
def test_wire_claim_cannot_grant_authority(name, field, value):
    body = {"CreativeDirective": directive_body(), "BrainDecision": brain_body(directive()),
            "ProductJourneyReceipt": receipt_body()}[name]
    body["authorization"][field] = value
    with pytest.raises(ContractError):
        seal_contract(name, body)


@pytest.mark.parametrize("mutation", ["fixture-upgrade", "source", "rights", "action", "digest"])
def test_provenance_rights_scope_and_digest_negative(mutation):
    body = directive_body()
    if mutation == "fixture-upgrade":
        body["provenance"]["actor"] = "FIXTURE"
    elif mutation == "source":
        body["provenance"]["source_sha"] = "0" * 40
    elif mutation == "rights":
        body["assets"][0]["rights"]["ref"] = None
    elif mutation == "action":
        body["scope"]["action_class"] = "PUBLISH"
    else:
        body = directive().to_dict()
        body["creative"]["freeform"] = "different bytes"
        with pytest.raises(ContractError, match="DIGEST_MISMATCH"):
            validate_contract("CreativeDirective", body)
        return
    with pytest.raises(ContractError):
        seal_contract("CreativeDirective", body)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}',
    b'[]', b'null', b'\xff', b'{', b'[' * 1500, b'x' * 131073],
    ids=["duplicate", "nan", "infinity", "array", "null", "utf8", "malformed", "depth", "oversize"])
def test_bounded_raw_json_negative(raw):
    with pytest.raises(ContractError):
        parse_contract("CreativeDirective", raw)


def test_all_states_declared_and_unknown_promotions_denied():
    assert tuple(state.value for state in State) == STATES
    with pytest.raises(TransitionError):
        Journey("fixture:journey", SOURCE, "APPROVED")
    state = waiting()
    with pytest.raises(TransitionError):
        event(state, "GO_PUBLISH")
    with pytest.raises(TransitionError):
        Event("e", EventKind.HOLD, state.journey_id, SOURCE, tool="override:state")
    with pytest.raises(TransitionError):
        step(state, EventKind.LEARNING_RECORDED, evidence=(QA,))


def test_missing_or_self_declared_brain_approval_stays_wait_brain():
    state = waiting()
    decision = seal_contract("BrainDecision", brain_body(state.directive))
    with pytest.raises(TransitionError, match="AUTHORITY"):
        step(state, EventKind.BRAIN_ACCEPTED,
             VerificationContext(current_chat_ref="chat:fixture-current"), record=decision)
    proof = authority(state, AuthorityKind.BRAIN, decision.to_dict()["decision_sha256"], verified=False)
    with pytest.raises(TransitionError, match="UNVERIFIED_AUTHORITY"):
        step(state, EventKind.BRAIN_ACCEPTED, VerificationContext((proof,), "chat:fixture-current"), record=decision)
    assert state.state == State.WAIT_BRAIN


@pytest.mark.parametrize("mutation", ["fixture", "human", "revise", "chat", "version", "scope", "source", "actor"])
def test_false_brain_identity_or_scope_cannot_promote(mutation):
    state = waiting()
    body = brain_body(state.directive)
    if mutation == "fixture":
        body["provenance"] = provenance("FIXTURE", "SYNTHETIC_FIXTURE")
    elif mutation == "human":
        body["provenance"] = provenance("HUMAN")
    elif mutation == "revise":
        body["decision"] = "REVISE"
    elif mutation == "chat":
        body["chat_ref"] = "chat:other"
    elif mutation == "version":
        body["directive_version"] = 2
    elif mutation == "scope":
        body["scope"]["account_ref"] = "account:other"
    elif mutation == "source":
        body["source_sha"] = body["provenance"]["source_sha"] = "0" * 40
    else:
        body["provenance"]["actor_ref"] = "brain:other"
    decision = seal_contract("BrainDecision", body)
    proof = authority(state, AuthorityKind.BRAIN, decision.to_dict()["decision_sha256"])
    with pytest.raises(TransitionError):
        step(state, EventKind.BRAIN_ACCEPTED, VerificationContext((proof,), "chat:fixture-current"), record=decision)


def test_hypothetical_attended_brain_then_offline_complete_preserves_fixture():
    state, context = ready()
    assert state.state == State.READY_FOR_HUMAN_RELEASE
    state = step(state, EventKind.OFFLINE_FINISHED, context)
    state = step(state, EventKind.LEARNING_RECORDED, evidence=(QA,))
    assert state.state == State.COMPLETE and state.effect_status == "NONE"
    assert state.evidence == (QA,) and state.evidence[0].evidence_type == "SYNTHETIC_FIXTURE"


@pytest.mark.parametrize("missing", [AuthorityKind.HUMAN_RELEASE, AuthorityKind.PROVIDER_ENTITLEMENT,
                                    AuthorityKind.ASSET_RIGHTS, AuthorityKind.BRAIN])
def test_publish_requires_each_separate_authority(missing):
    state, context = ready(True)
    context = release_context(state, context)
    context = replace(context, receipts=tuple(r for r in context.receipts if r.kind != missing))
    with pytest.raises(TransitionError, match="AUTHORITY"):
        release(state, context)
    assert state.effect_status == "NONE"


@pytest.mark.parametrize("mutation", ["expired", "unverified", "fixture", "scope", "provider", "channel", "duplicate"])
def test_publish_authority_negative(mutation):
    state, context = ready(True)
    context = release_context(state, context)
    receipts = list(context.receipts)
    changes = {"expired": {"expires_at": 5}, "unverified": {"verified": False},
        "fixture": {"evidence_type": "SYNTHETIC_FIXTURE"}, "scope": {"scope_sha256": "0" * 64},
        "provider": {"actor_ref": "provider:other"}, "channel": {"channel": "PUBLIC_RUNNER"}}
    if mutation == "duplicate":
        receipts.append(receipts[2])
    else:
        receipts[2] = replace(receipts[2], **changes[mutation])
    with pytest.raises(TransitionError):
        release(state, replace(context, receipts=tuple(receipts)))


def test_publish_exact_scope_tool_and_separate_spend_ceiling():
    state, context = ready(True)
    context = release_context(state, context)
    with pytest.raises(TransitionError, match="TOOL"):
        release(state, context, tool="tool:unlisted")
    with pytest.raises(TransitionError, match="SPEND"):
        release(state, context, spend_minor=1, currency="USD")
    receipts = tuple(replace(r, actions=("GO_PUBLISH", "GO_SPEND"), spend_ceiling_minor=5, currency="USD")
                     if r.kind == AuthorityKind.HUMAN_RELEASE else r for r in context.receipts)
    context = replace(context, receipts=receipts)
    assert release(state, context, spend_minor=5, currency="USD").state == State.RELEASE_INTENT
    for spend, currency in ((6, "USD"), (5, "EUR")):
        with pytest.raises(TransitionError, match="SPEND"):
            release(state, context, spend_minor=spend, currency=currency)
    offline, offline_context = ready()
    with pytest.raises(TransitionError, match="PUBLISH_SCOPE"):
        release(offline, release_context(offline, offline_context))


def test_unknown_blocks_replay_rekey_completion_and_false_confirmation():
    state, context = pending()
    state = step(state, EventKind.EFFECT_UNCERTAIN, effect_key=state.effect_key,
                 effect_request_sha256=state.effect_request_sha256)
    assert state.state == State.UNKNOWN_EFFECT
    for kind in (EventKind.RELEASE_REQUESTED, EventKind.LEARNING_RECORDED, EventKind.OFFLINE_FINISHED):
        with pytest.raises(TransitionError):
            step(state, kind)
    with pytest.raises(TransitionError):
        release(state, context, effect_key="effect:rekey")
    confirmation, verified = observe(state, context)
    with pytest.raises(TransitionError, match="AUTHORITY"):
        transition(state, confirmation, at=10, verification=context)
    with pytest.raises(TransitionError):
        transition(state, replace(confirmation, effect_key="effect:other"), at=10, verification=verified)
    with pytest.raises(TransitionError):
        transition(state, replace(confirmation, evidence=(replace(confirmation.evidence[0],
            evidence_type="HUMAN_SUPPLIED", attestation_ref=None),)), at=10, verification=verified)


@pytest.mark.parametrize("mutation", ["outcome", "request", "key", "verifier", "fixture"])
def test_provider_label_and_verified_bytes_cannot_change_observed_outcome(mutation):
    state, context = pending()
    confirmation, context = observe(state, context)
    proof = context.receipts[-1]
    changes = {"outcome": {"effect_outcome": "NO_EFFECT"}, "request": {"effect_request_sha256": "0" * 64},
        "key": {"effect_key": "effect:other"}, "verifier": {"verifier_ref": "verifier:other"},
        "fixture": {"evidence_type": "SYNTHETIC_FIXTURE"}}
    context = replace(context, receipts=(*context.receipts[:-1], replace(proof, **changes[mutation])))
    with pytest.raises(TransitionError):
        transition(state, confirmation, at=10, verification=context)


def test_hypothetical_confirmed_path_and_no_effect_recovery_are_distinct():
    state, context = pending()
    uncertain = step(state, EventKind.EFFECT_UNCERTAIN, effect_key=state.effect_key,
                     effect_request_sha256=state.effect_request_sha256)
    confirmation, verified = observe(uncertain, context)
    confirmed = transition(uncertain, confirmation, at=10, verification=verified)
    assert confirmed.state == State.RELEASE_CONFIRMED
    analytics = EvidencePin("DISTRIBUTION", "fixture:analytics", "1" * 64, "HUMAN_SUPPLIED")
    measured = step(confirmed, EventKind.ANALYTICS_ACCEPTED, evidence=(analytics,))
    learning = step(measured, EventKind.REVIEW_LEARNING, evidence=(QA,))
    assert step(learning, EventKind.LEARNING_RECORDED, evidence=(QA,)).state == State.COMPLETE
    reconciliation, verified = observe(uncertain, context, EventKind.RECONCILED_NO_EFFECT)
    held = transition(uncertain, reconciliation, at=10, verification=verified)
    assert held.state == State.HOLD and held.effect_status == "NO_EFFECT"
    assert held.effect_keys == ("effect:one",)
    with pytest.raises(TransitionError, match="TERMINAL"):
        release(held, verified)


def test_event_replay_determinism_no_mutation_and_no_arbitrary_override():
    state = Journey("fixture:journey", SOURCE)
    incoming = event(state, EventKind.INPUT_ACCEPTED, record=directive())
    result = transition(state, incoming, at=10)
    assert transition(state, incoming, at=10) == result
    assert state.state == State.NEW and state.seen_event_ids == ()
    with pytest.raises(TransitionError, match="REPLAY"):
        transition(result, incoming, at=10)
    with pytest.raises(TransitionError):
        transition(state, replace(incoming, source_sha="0" * 40), at=10)
    with pytest.raises(TransitionError):
        transition(state, incoming, at=True)
    with pytest.raises(FrozenInstanceError):
        result.state = State.COMPLETE


def test_evidence_cannot_be_upgraded_by_another_event_and_stop_preserves_unknown():
    state = Journey("fixture:journey", SOURCE)
    state = step(state, EventKind.INPUT_ACCEPTED, record=directive())
    state = step(state, EventKind.START, tool="tool:offline")
    state = step(state, EventKind.OUTPUT_READY, evidence=(QA,))
    with pytest.raises(TransitionError, match="PROVENANCE_CONFLICT"):
        step(state, EventKind.QA_PASSED, evidence=(replace(QA, evidence_type="HUMAN_SUPPLIED"),))
    state, _ = pending()
    state = step(state, EventKind.EFFECT_UNCERTAIN, effect_key=state.effect_key,
                 effect_request_sha256=state.effect_request_sha256)
    stopped = step(state, EventKind.STOP)
    assert stopped.state == State.STOPPED and stopped.effect_status == "UNKNOWN"
    assert stopped.effect_key == state.effect_key


def test_contract_validation_and_transition_perform_no_io(monkeypatch):
    initial = Journey("fixture:journey", SOURCE)
    record = directive()
    incoming = event(initial, EventKind.INPUT_ACCEPTED, record=record)
    def deny(*args, **kwargs):
        pytest.fail("B0.1 attempted I/O or process dispatch")
    with monkeypatch.context() as patch:
        for target, method in ((builtins, "open"), (io, "open"), (os, "open"),
            (Path, "open"), (Path, "read_bytes"), (Path, "write_bytes"),
            (sqlite3, "connect"), (subprocess, "Popen")):
            patch.setattr(target, method, deny)
        assert parse_contract("CreativeDirective", record.raw) == record
        assert seal_contract("BrainDecision", brain_body(record)).name == "BrainDecision"
        assert seal_contract("ProductJourneyReceipt", receipt_body()).name == "ProductJourneyReceipt"
        assert transition(initial, incoming, at=10).state == State.INPUT_VALIDATED


def test_source_has_no_effect_adapters_or_implicit_dependency():
    allowed = {"__future__", "dataclasses", "enum", "hashlib", "json", "math", "re", "jsonschema", "referencing"}
    for path in (ROOT / "src/ai_commerce/production_runtime").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in allowed for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                assert node.module.split(".")[0] in allowed
