"""Synthetic D5 exact-chat chip staging tests; no browser, network or Send."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from ai_commerce.remote.conversation_binding import ResolvedBinding
from ai_commerce.remote.plugin_turn_handoff import (
    new_session, record_send_intent, reconcile_ui_observation,
)
from ai_commerce.remote import plugin_turn_stage_only as stage

URL = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001"
BIND = ResolvedBinding(
    route_id="operator-registered", generation=1,
    chat_url=URL, cdp_endpoint="http://127.0.0.1:9333")


def session(index=0):
    result = new_session(
        handoff_id="d5-turn-" + "a" * 32,
        issue_number=181, source_sha="b" * 40)
    for i in range(index):
        result = record_send_intent(result, operator_approved=True)
        result = reconcile_ui_observation(
            result, operator_verified=True,
            issue_comment_ref=(
                "https://github.com/trung-via/ai-commerce/issues/181"
                f"#issuecomment-{100+i}"))
    return result


class Editor:
    def __init__(self, page):
        self.page = page

    def count(self):
        return 1

    def focus(self, **kwargs):
        self.page.actions.append("focus")

    def press_sequentially(self, value, **kwargs):
        self.page.actions.append(("type", value))
        if self.page.error == "TYPE_NAME":
            raise TimeoutError("private URL must not escape")
        self.page.text = value if self.page.error != "INCOMPLETE" else value[:-3]

    def press(self, value, **kwargs):
        self.page.actions.append(("press", value))
        assert value == "Space"
        self.page.chip = True


class Page:
    def __init__(self, *, url=URL, error=None, precheck="READY", observed=None):
        self.url, self.error, self.precheck, self.observed = url, error, precheck, observed
        self.actions, self.text, self.chip = [], "", False

    def locator(self, selector):
        assert "contenteditable" in selector
        return Editor(self)

    def evaluate(self, script, args):
        self.actions.append("evaluate")
        assert args["url"] == URL
        if script == stage._PRECHECK_JS:
            if args["phase"] == "EMPTY":
                return self.precheck
            assert args["phase"] == "FULL_NAME"
            return "FULL_NAME_PRESENT" if self.text in (
                args["full"], args["name"]) else "FULL_NAME_UNPROVEN"
        if script == stage._SEND_PRECHECK:
            return ("READY" if self.chip and
                    self.text == args["prompt"] and
                    self.observed != "DRAFT_MISMATCH"
                    else "DRAFT_MISMATCH")
        if self.observed is not None:
            return self.observed
        assert self.chip
        return {
            "code": "PROBE_COMPLETE",
            "brain": ("STRUCTURED_CANDIDATE"
                      if args["name"] == stage.BRAIN else "ABSENT"),
            "engineering": ("STRUCTURED_CANDIDATE"
                            if args["name"] == stage.ENGINEERING else "ABSENT"),
        }

    def wait_for_timeout(self, milliseconds):
        assert milliseconds == 700
        self.actions.append("wait")


class Inventory:
    def __init__(self, *pages):
        self.pages_list = list(pages)
        self.attached = 0

    @contextmanager
    def pages(self, endpoint):
        self.attached += 1
        assert endpoint == "http://127.0.0.1:9333"
        yield self.pages_list


@pytest.fixture(autouse=True)
def pretend_windows(monkeypatch):
    # Actual production runtime checks os.name == "nt".
    monkeypatch.setattr(stage, "_is_windows", lambda: True)


@pytest.mark.parametrize("index,expected", [
    (0, "@AI Commerce Brain"),
    (1, "@AI Commerce Engineering Workflow"),
    (2, "@AI Commerce Brain"),
])
def test_stages_correct_single_chip_for_turn(index, expected):
    page = Page()
    inv = Inventory(page)
    result = stage.ExplicitTurnChipStager(enabled=True, inventory=inv).stage(
        BIND, session(index), operator_approved=True)
    assert (result.code, result.phase) == ("INPUT_STAGED_NO_SEND", "COMPLETE")
    assert result.host_selection_proven is False and result.sent is False
    assert ("type", expected) in page.actions
    assert page.actions.count(("press", "Space")) == 1
    assert result.brain == ("STRUCTURED_CANDIDATE"
                            if index != 1 else "ABSENT")
    assert result.engineering == ("STRUCTURED_CANDIDATE"
                                  if index == 1 else "ABSENT")


@pytest.mark.parametrize("enabled,approved,code", [
    (False, True, "STAGER_DISABLED"),
    (True, False, "OPERATOR_APPROVAL_REQUIRED"),
    (True, 1, "OPERATOR_APPROVAL_REQUIRED"),
])
def test_opt_in_guards_no_browser_attach(enabled, approved, code):
    inv = Inventory(Page())
    result = stage.ExplicitTurnChipStager(enabled=enabled, inventory=inv).stage(
        BIND, session(), operator_approved=approved)
    assert result.code == code and inv.attached == 0


def test_wont_stage_when_submission_unknown():
    pending = record_send_intent(session(), operator_approved=True)
    inv = Inventory(Page())
    result = stage.ExplicitTurnChipStager(enabled=True, inventory=inv).stage(
        BIND, pending, operator_approved=True)
    assert result.code == "TURN_NOT_READY" and inv.attached == 0


@pytest.mark.parametrize("page_list", [[], [Page(url=URL), Page(url=URL)],
                                       [Page(url=URL.replace("0001", "0002"))]])
def test_unique_exact_tab_required(page_list):
    inv = Inventory(*page_list)
    result = stage.ExplicitTurnChipStager(enabled=True, inventory=inv).stage(
        BIND, session(), operator_approved=True)
    assert result.code == "EXACT_CHAT_NOT_UNIQUE"


def test_preexisting_draft_blocks_all_keypresses():
    page = Page(precheck="DRAFT_PRESENT")
    result = stage.ExplicitTurnChipStager(
        enabled=True, inventory=Inventory(page)).stage(
            BIND, session(), operator_approved=True)
    assert result.code == "DRAFT_PRESENT"
    assert not any(a == "focus" or isinstance(a, tuple) for a in page.actions)


def test_full_name_must_exist_before_space():
    page = Page(error="INCOMPLETE")
    result = stage.ExplicitTurnChipStager(
        enabled=True, inventory=Inventory(page)).stage(
            BIND, session(), operator_approved=True)
    assert (result.code, result.phase) == (
        "INCOMPLETE_NAME_CLEAR_MANUALLY", "VERIFY_NAME")
    assert not page.chip and not any(
        isinstance(a, tuple) and a[0] == "press" for a in page.actions)


def test_type_error_fails_closed_and_never_retries():
    page = Page(error="TYPE_NAME")
    result = stage.ExplicitTurnChipStager(
        enabled=True, inventory=Inventory(page)).stage(
            BIND, session(), operator_approved=True)
    assert (result.code, result.phase) == (
        "STAGING_UNKNOWN_CLEAR_MANUALLY", "TYPE_NAME")
    assert page.actions.count(("type", "@AI Commerce Brain")) == 1
    assert "private" not in repr(result)


def test_invalid_browser_probe_never_proves_plugin():
    page = Page(observed={"code": "PROBE_COMPLETE", "brain": "STRUCTURED_CANDIDATE",
                          "engineering": "ABSENT", "transcript": "NO"})
    result = stage.ExplicitTurnChipStager(
        enabled=True, inventory=Inventory(page)).stage(
            BIND, session(), operator_approved=True)
    assert result.code == "OBSERVATION_UNKNOWN_CLEAR_MANUALLY"
    assert not result.host_selection_proven


def test_ambiguous_chip_does_not_claim_staging_pass():
    page = Page(observed={"code": "PROBE_COMPLETE", "brain": "AMBIGUOUS",
                          "engineering": "ABSENT"})
    result = stage.ExplicitTurnChipStager(
        enabled=True, inventory=Inventory(page)).stage(
            BIND, session(), operator_approved=True)
    assert result.code == "CHIP_UNVERIFIED_CLEAR_MANUALLY"
    assert result.brain == "AMBIGUOUS"


def test_stage_source_has_no_send_or_wake_hooks():
    import inspect
    source = inspect.getsource(stage)
    assert "button.click(" not in source
    assert 'press("Enter")' not in source
    assert "window.fetch(" not in source
    assert "subprocess" not in source
    assert "send_one(" not in source
    assert "page.goto(" not in source


def test_prepare_turn_stages_one_chip_and_exact_prompt_without_send():
    from ai_commerce.remote.plugin_turn_handoff import preview_next_turn

    page = Page()
    state = session(index=1)
    result = stage.ExplicitTurnChipStager(
        enabled=True, inventory=Inventory(page)).stage(
            BIND, state, operator_approved=True, include_prompt=True)
    assert result.code == "TURN_PREPARED_NO_SEND" and result.sent is False
    assert result.host_selection_proven is False
    assert ("type", preview_next_turn(state)["prompt"]) in page.actions
    assert page.actions.count(("press", "Space")) == 1


def test_prepare_turn_bad_exact_draft_stops_without_click():
    page = Page(observed="DRAFT_MISMATCH")
    # Fake structural chip observation and failed drafted-text validation.
    old_eval = page.evaluate
    def bad_after_chip(script, args):
        if script == stage._SEND_PRECHECK:
            return "DRAFT_MISMATCH"
        return old_eval(script, args)
    page.evaluate = bad_after_chip
    result = stage.ExplicitTurnChipStager(
        enabled=True, inventory=Inventory(page)).stage(
            BIND, session(), operator_approved=True, include_prompt=True)
    assert result.code == "DRAFT_UNVERIFIED_CLEAR_MANUALLY"
    assert result.sent is False
