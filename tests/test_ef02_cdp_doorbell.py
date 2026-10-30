"""Deterministic adapter tests: no real Chrome/account/message is involved."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from ai_commerce.remote.cdp_doorbell import LocalChromeDoorbellAdapter
from ai_commerce.remote.conversation_binding import ResolvedBinding

URL = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001"
OTHER = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000002"
EVENT = "ef02-completion-v1:" + "a" * 64
BIND = ResolvedBinding(
    route_id="route-A", generation=1, chat_url=URL,
    cdp_endpoint="http://127.0.0.1:9222",
)


class FakePage:
    def __init__(self, url=URL, *, gate="READY", proof=True, fail_step=None):
        self.url = url
        self.gate = gate
        self.proof = proof
        self.fail_step = fail_step
        self.counts = {"insert": 0, "click": 0, "prove": 0}
        self.submitted_text = None
        self.staged = False
        self.clicked = False

    def set_default_timeout(self, n):
        assert n == 3000

    def evaluate(self, script, args):
        assert args["url"] == URL
        assert args["text"].startswith("[AI COMMERCE LOCAL CHAT WAKE]")
        if "return 'READY'" in script:
            return self.gate
        if "document.execCommand(" in script:
            self.counts["insert"] += 1
            if self.fail_step == "insert":
                raise OSError("synthetic Chrome disconnect")
            self.staged = True
            return True
        if "button.click()" in script:
            self.counts["click"] += 1
            if self.fail_step == "click":
                raise OSError("synthetic ambiguous click")
            self.clicked = True
            self.submitted_text = args["text"]
            return True
        if "return found.length===1" in script:
            self.counts["prove"] += 1
            return self.proof and self.clicked
        if "return result.buttons.length === 1" in script:
            return self.staged
        raise AssertionError("unknown script execution")

    def wait_for_function(self, script, *, arg, timeout):
        assert timeout in (3000, 5000)
        if self.fail_step == "wait_ready" and timeout == 3000:
            raise TimeoutError("insert not accepted")
        if self.fail_step == "wait_proof" and timeout == 5000:
            raise TimeoutError("no visible user turn")
        assert self.evaluate(script, arg) is True


class FakeInventory:
    def __init__(self, *pages):
        self._pages = list(pages)
        self.calls = []

    @contextmanager
    def pages(self, endpoint):
        self.calls.append(endpoint)
        assert endpoint == BIND.cdp_endpoint
        yield self._pages


def call(inventory, *, enabled=True, event=EVENT):
    return LocalChromeDoorbellAdapter(
        allow_submit=enabled, inventory=inventory,
    ).send_wake(binding=BIND, message="caller-controlled result or diagnostic",
                event_id=event)


def test_doorbell_is_opt_in_and_does_not_attach_by_default():
    inv = FakeInventory(FakePage())
    outcome = call(inv, enabled=False)
    assert outcome.delivery_status == "UNSUPPORTED"
    assert outcome.error_code == "CDP_PROBE_DISABLED"
    assert inv.calls == []


def test_exact_chat_only_and_no_new_tabs_or_navigation():
    wrong = FakePage(OTHER)
    inv = FakeInventory(wrong)
    receipt = call(inv)
    assert receipt.error_code == "EXACT_CHAT_TAB_NOT_FOUND"
    assert wrong.counts["click"] == 0
    same1, same2 = FakePage(), FakePage()
    outcome = call(FakeInventory(same1, same2))
    assert outcome.error_code == "AMBIGUOUS_CHAT_TARGETS"
    assert same1.counts["click"] == same2.counts["click"] == 0


@pytest.mark.parametrize("gate", [
    "DRAFT_PRESENT", "GENERATION_ACTIVE", "SURFACE_UNPROVEN",
    "TARGET_PAGE_CHANGED", "OUTBOUND_ALREADY_PRESENT",
])
def test_unsafe_ui_never_stages_or_clicks(gate):
    page = FakePage(gate=gate)
    result = call(FakeInventory(page))
    assert result.delivery_status == "UNSUPPORTED"
    assert result.error_code == gate
    assert page.counts["insert"] == 0
    assert page.counts["click"] == 0


def test_exact_user_turn_observed_is_only_unknown_not_server_confirmed():
    page = FakePage()
    receipt = call(FakeInventory(FakePage(OTHER), page))
    assert receipt.delivery_status == "UNKNOWN"
    assert receipt.error_code == "UI_USER_TURN_OBSERVED"
    assert receipt.chat_delivery_proven is False
    assert receipt.brain_continuation_proven is False
    assert page.counts == {"insert": 1, "click": 1, "prove": 2}
    assert "event_id: " + EVENT in page.submitted_text
    assert "repository: trung-via/ai-commerce" in page.submitted_text
    assert "fresh_brain_sync_required: true" in page.submitted_text
    assert "caller-controlled" not in page.submitted_text
    assert "candidate_sha" not in page.submitted_text


@pytest.mark.parametrize("failure,expected_code,expected_click", [
    ("insert", "INSERT_BLOCKED", 0),
    ("wait_ready", "INSERT_BLOCKED", 0),
    ("click", "SUBMISSION_UNPROVEN", 1),
    ("wait_proof", "SUBMISSION_UNPROVEN", 1),
])
def test_uncertain_submissions_never_claim_confirmed(failure, expected_code, expected_click):
    page = FakePage(fail_step=failure)
    receipt = call(FakeInventory(page))
    assert receipt.delivery_status == "UNKNOWN"
    assert receipt.error_code == expected_code
    assert receipt.chat_delivery_proven is False
    assert page.counts["click"] == expected_click


def test_invalid_event_rejected_without_attaching():
    page = FakePage()
    inv = FakeInventory(page)
    result = call(inv, event="user-controlled-unbounded")
    assert result.delivery_status == "UNSUPPORTED"
    assert result.chat_delivery_proven is False
    assert inv.calls == []
