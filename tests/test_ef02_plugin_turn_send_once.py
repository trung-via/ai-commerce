"""D5 one-shot Sender fail-closed tests: fake browser, no ChatGPT Send."""
from contextlib import contextmanager
from copy import deepcopy

import pytest

from ai_commerce.remote.conversation_binding import ResolvedBinding
from ai_commerce.remote.plugin_turn_handoff import (
    TurnHandoffError, new_session, record_send_intent,
)
from ai_commerce.remote import plugin_turn_send_once as send


URL = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001"
BIND = ResolvedBinding(
    route_id="enrolled", generation=1, chat_url=URL,
    cdp_endpoint="http://127.0.0.1:9333")


def ready():
    return new_session(
        handoff_id="d5-turn-" + "a" * 32,
        issue_number=181, source_sha="b" * 40)


class Store:
    def __init__(self, state):
        self.state = deepcopy(state)
        self.events = []

    def read(self):
        self.events.append("read")
        return deepcopy(self.state)

    def record_intent_checked(self, *, expected_state, operator_approved=False):
        self.events.append("CAS")
        if not operator_approved:
            raise TurnHandoffError("TURN_OPERATOR_APPROVAL_REQUIRED")
        if self.state != expected_state:
            raise TurnHandoffError("TURN_STATE_CHANGED")
        self.state = record_send_intent(self.state, operator_approved=True)
        return deepcopy(self.state)


class Page:
    def __init__(self, store, *, code="READY", click="OK", url=URL):
        self.url = url
        self.store = store
        self.code, self.click = code, click
        self.events = []

    def evaluate(self, script, args):
        assert args["url"] == URL and args["name"] == "AI Commerce Brain"
        assert args["prompt"].startswith("[AI COMMERCE D5 EXPLICIT ROLE HANDOFF]")
        if script == send._PRECHECK_JS:
            self.events.append("PRECHECK")
            return self.code
        assert script == send._CLICK_JS
        self.events.append("CLICK")
        assert self.store.state["phase"] == "SUBMISSION_UNKNOWN"
        if self.click == "ERROR":
            raise TimeoutError("private browser details")
        return self.click == "OK"


class Inventory:
    def __init__(self, *pages):
        self.pages_list = pages
        self.attached = 0

    @contextmanager
    def pages(self, endpoint):
        assert endpoint == BIND.cdp_endpoint
        self.attached += 1
        yield self.pages_list


@pytest.fixture(autouse=True)
def windows(monkeypatch):
    monkeypatch.setattr(send, "_is_windows", lambda: True)


def test_disabled_has_no_browser_or_ledger_access():
    store = Store(ready())
    inventory = Inventory()
    result = send.ExplicitTurnOneShotSender(
        inventory=inventory).send_once(
            store=store, expected_state=ready(), binding=BIND,
            current_main_sha=lambda: "b" * 40,
            current_binding=lambda: BIND, operator_approved=True)
    assert result.code == "SEND_DISABLED"
    assert inventory.attached == 0 and store.events == []


@pytest.mark.parametrize("approved", [False, 1, None])
def test_requires_exact_approval_before_any_read(approved):
    store = Store(ready())
    result = send.ExplicitTurnOneShotSender(enabled=True).send_once(
        store=store, expected_state=ready(), binding=BIND,
        current_main_sha=lambda: "b" * 40,
        current_binding=lambda: BIND, operator_approved=approved)
    assert result.code == "OPERATOR_APPROVAL_REQUIRED"
    assert store.events == []


def test_stale_main_prevents_browser_and_write():
    store = Store(ready())
    inv = Inventory()
    result = send.ExplicitTurnOneShotSender(enabled=True, inventory=inv).send_once(
        store=store, expected_state=ready(), binding=BIND,
        current_main_sha=lambda: "c" * 40,
        current_binding=lambda: BIND, operator_approved=True)
    assert result.code == "SOURCE_SHA_CHANGED"
    assert store.state["phase"] == "READY" and inv.attached == 0


def test_revoked_route_prevents_browser_and_write():
    store = Store(ready())
    inv = Inventory()
    result = send.ExplicitTurnOneShotSender(enabled=True, inventory=inv).send_once(
        store=store, expected_state=ready(), binding=BIND,
        current_main_sha=lambda: "b" * 40,
        current_binding=lambda: ResolvedBinding(
            route_id="other", generation=1,
            chat_url=URL, cdp_endpoint=BIND.cdp_endpoint),
        operator_approved=True)
    assert result.code == "ROUTE_STATE_CHANGED"
    assert store.state["phase"] == "READY" and inv.attached == 0


def test_unknown_state_never_attaches_or_replays():
    state = record_send_intent(ready(), operator_approved=True)
    store = Store(state)
    inv = Inventory()
    result = send.ExplicitTurnOneShotSender(enabled=True, inventory=inv).send_once(
        store=store, expected_state=state, binding=BIND,
        current_main_sha=lambda: "b" * 40,
        current_binding=lambda: BIND, operator_approved=True)
    assert result.code == "TURN_NOT_READY"
    assert inv.attached == 0 and "CAS" not in store.events


@pytest.mark.parametrize("code", ["DRAFT_MISMATCH", "CHIP_UNVERIFIED",
                                  "GENERATION_ACTIVE", "SEND_BUTTON_UNPROVEN"])
def test_unverified_composer_never_records_intent(code):
    store = Store(ready())
    page = Page(store, code=code)
    result = send.ExplicitTurnOneShotSender(
        enabled=True, inventory=Inventory(page)).send_once(
            store=store, expected_state=ready(), binding=BIND,
            current_main_sha=lambda: "b" * 40,
            current_binding=lambda: BIND, operator_approved=True)
    assert result.code == code
    assert store.state["phase"] == "READY"
    assert "CLICK" not in page.events and "CAS" not in store.events


@pytest.mark.parametrize("pages", [[], [URL, URL],
                                     ["https://chatgpt.com/c/00000000-0000-0000-0000-000000000002"]])
def test_exact_one_chat_required(pages):
    store = Store(ready())
    inv = Inventory(*(Page(store, url=url) for url in pages))
    result = send.ExplicitTurnOneShotSender(enabled=True, inventory=inv).send_once(
        store=store, expected_state=ready(), binding=BIND,
        current_main_sha=lambda: "b" * 40,
        current_binding=lambda: BIND, operator_approved=True)
    assert result.code == "EXACT_CHAT_NOT_UNIQUE"
    assert "CAS" not in store.events


def test_single_click_only_after_durable_unknown_and_no_replay():
    store = Store(ready())
    page = Page(store)
    inv = Inventory(page)
    gate = send.ExplicitTurnOneShotSender(enabled=True, inventory=inv)
    kwargs = dict(store=store, expected_state=ready(), binding=BIND,
                  current_main_sha=lambda: "b" * 40,
                  current_binding=lambda: BIND, operator_approved=True)
    first = gate.send_once(**kwargs)
    assert first.code == "SEND_OUTCOME_UNKNOWN"
    assert first.phase == "SUBMISSION_UNKNOWN" and first.sent is None
    assert not first.host_selection_proven
    assert store.state["phase"] == "SUBMISSION_UNKNOWN"
    assert store.events.index("CAS") < len(store.events)
    assert page.events == ["PRECHECK", "PRECHECK", "CLICK"]
    second = gate.send_once(**kwargs)
    assert second.code == "TURN_STATE_CHANGED"
    assert page.events == ["PRECHECK", "PRECHECK", "CLICK"]


@pytest.mark.parametrize("outcome", ["ERROR", "FALSE"])
def test_click_ambiguous_never_retries(outcome):
    store = Store(ready())
    page = Page(store, click=outcome)
    gate = send.ExplicitTurnOneShotSender(enabled=True, inventory=Inventory(page))
    result = gate.send_once(
        store=store, expected_state=ready(), binding=BIND,
        current_main_sha=lambda: "b" * 40,
        current_binding=lambda: BIND, operator_approved=True)
    assert result.phase == "SUBMISSION_UNKNOWN" and result.sent is None
    assert result.code in ("SEND_OUTCOME_UNKNOWN", "SEND_BLOCKED_UNKNOWN")
    assert page.events.count("CLICK") == 1
    assert store.state["phase"] == "SUBMISSION_UNKNOWN"


def test_route_drift_during_browser_preflight_stops_before_cas():
    store = Store(ready())
    page = Page(store)
    seen = [0]
    def route():
        seen[0] += 1
        return BIND if seen[0] == 1 else ResolvedBinding(
            route_id="other", generation=1,
            chat_url=URL, cdp_endpoint=BIND.cdp_endpoint)
    result = send.ExplicitTurnOneShotSender(
        enabled=True, inventory=Inventory(page)).send_once(
            store=store, expected_state=ready(), binding=BIND,
            current_main_sha=lambda: "b" * 40,
            current_binding=route, operator_approved=True)
    assert result.code == "PREFLIGHT_CHANGED"
    assert store.state["phase"] == "READY" and "CLICK" not in page.events


def test_only_click_script_mutates_and_guard_checks_exact_payload():
    assert "button.click()" in send._CLICK_JS
    assert "button.click()" not in send._PRECHECK_JS
    assert "args.prompt" in send._PRECHECK_JS
    assert "args.name" in send._CLICK_JS
    assert "other" in send._CLICK_JS


def test_cas_exception_is_unknown_and_does_not_click():
    store = Store(ready())
    page = Page(store)
    def uncertain(**kwargs):
        store.state = record_send_intent(store.state, operator_approved=True)
        raise TurnHandoffError("PRIVATE_STORE_WRITE_UNCERTAIN")
    store.record_intent_checked = uncertain
    result = send.ExplicitTurnOneShotSender(
        enabled=True, inventory=Inventory(page)).send_once(
            store=store, expected_state=ready(), binding=BIND,
            current_main_sha=lambda: "b" * 40,
            current_binding=lambda: BIND, operator_approved=True)
    assert result.code == "INTENT_STATUS_UNKNOWN"
    assert result.sent is None and result.phase == "UNKNOWN"
    assert store.state["phase"] == "SUBMISSION_UNKNOWN"
    assert "CLICK" not in page.events
