"""Origin-affine reusable draft contract; all inputs are synthetic, no provider effects."""
import json
import pytest

# Origin-first reusable future-workflow correlation (Issue #489).
# These are source-only fixtures. No genuine ChatGPT origin, completion,
# consent, private registry or browser effect is established by these tests.
from pathlib import Path as _OriginPath
from jsonschema import Draft202012Validator as _OriginDraftValidator
from ai_commerce.origin_affinity.contracts import (
    OriginTicketError, draft_return_ticket, validate_draft_return_ticket,
)


def _origin_ticket(**updates):
    fields = dict(route_id="routeA", generation=1,
                  origin_method="OPAQUE_RENDEZVOUS_V1",
                  origin_proof_sha256="a" * 64,
                  work_namespace="EF02", work_id="batchA",
                  event_ref="ef02-completion-v1:" + "b" * 64,
                  completion_evidence_sha256="c" * 64, source_sha="d" * 40)
    fields.update(updates)
    return draft_return_ticket(**fields)


def test_origin_affinity_ticket_is_closed_inert_schema_v1():
    schema = json.loads(
        (_OriginPath(__file__).resolve().parents[1]
         / "contracts" / "OriginReturnTicket.v1.schema.json").read_text(encoding="utf-8"))
    _OriginDraftValidator.check_schema(schema)
    validator = _OriginDraftValidator(schema)
    ticket = _origin_ticket()
    assert not list(validator.iter_errors(ticket))
    assert validate_draft_return_ticket(ticket) == ticket
    assert ticket["authorization"] == "NOT_VERIFIED"
    assert ticket["effect"] == "NOT_ATTEMPTED"
    assert ticket["delivery_mode"] == "SEND_ONLY"
    encoded = json.dumps(ticket)
    for forbidden in ("chatgpt.com", "cookie", "chat_url", "account_id",
                      "session_id", "password", "token", "9333"):
        assert forbidden not in encoded
    assert ticket == _origin_ticket()


def test_origin_affinity_futures_are_workflow_neutral_but_route_pinned():
    first = _origin_ticket()
    second = _origin_ticket(work_namespace="PULSE", work_id="journeyA")
    same_route_new_work = _origin_ticket(work_id="batchB")
    different_chat = _origin_ticket(route_id="routeB")
    new_generation = _origin_ticket(generation=2)
    separate_method = _origin_ticket(origin_method="PAGE_SCOPED_GESTURE_V1")
    keys = {row["delivery_key"] for row in (first, second, same_route_new_work,
                                             different_chat, new_generation, separate_method)}
    assert len(keys) == 6
    assert second["route_id"] == first["route_id"] == same_route_new_work["route_id"]
    assert second["work_namespace"] == "PULSE"
    assert new_generation["generation"] == 2


@pytest.mark.parametrize("change", [
    {"route_id": "route/other"}, {"route_id": "https://chatgpt.com/c/foo"},
    {"generation": True}, {"generation": 0}, {"generation": 2**31},
    {"origin_method": "HOST_SESSION_METADATA_ONLY"},
    {"origin_method": "OTHER"},
    {"origin_proof_sha256": "A" * 64},
    {"work_namespace": "foo"}, {"work_id": "unsafe/work"},
    {"event_ref": "https://chatgpt.com/c/id"},
    {"completion_evidence_sha256": "missing"},
    {"source_sha": "bad"},
])
def test_origin_affinity_rejects_unsafe_or_ambiguous_fields(change):
    with pytest.raises(OriginTicketError):
        _origin_ticket(**change)


@pytest.mark.parametrize("key,value", [
    ("authorization", "VERIFIED"),
    ("effect", "USER_TURN_OBSERVED"),
    ("delivery_mode", "AUTO_BRAIN_EXECUTE"),
    ("route_id", "routeB"),
    ("generation", 2),
    ("source_sha", "1" * 40),
    ("event_ref", "event:replay"),
    ("delivery_key", "0" * 64),
])
def test_origin_affinity_rejects_mutated_drafts_not_a_signing_grant(key, value):
    ticket = _origin_ticket()
    ticket[key] = value
    with pytest.raises(OriginTicketError):
        validate_draft_return_ticket(ticket)


def test_origin_affinity_denies_extra_sensitive_fields_and_missing_fields():
    ticket = _origin_ticket()
    schema = json.loads(
        (_OriginPath(__file__).resolve().parents[1]
         / "contracts" / "OriginReturnTicket.v1.schema.json").read_text(encoding="utf-8"))
    for payload in [
        {**ticket, "chat_url": "https://chatgpt.com/c/abc"},
        {**ticket, "cookie": "secret"},
        {**ticket, "host_session": "private"},
        {k: v for k, v in ticket.items() if k != "origin_proof_sha256"},
    ]:
        assert not _OriginDraftValidator(schema).is_valid(payload)
        with pytest.raises(OriginTicketError):
            validate_draft_return_ticket(payload)


def test_origin_affinity_record_does_not_authorize_live_wake():
    ticket = _origin_ticket(work_namespace="TEST_ONLY", work_id="fixture")
    assert ticket["effect"] == "NOT_ATTEMPTED"
    assert ticket["authorization"] == "NOT_VERIFIED"
    assert ticket["origin_proof_sha256"] == "a" * 64  # a fixture digest, not a proof
    assert validate_draft_return_ticket(ticket)["delivery_key"] == ticket["delivery_key"]
