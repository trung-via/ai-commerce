"""Synthetic D5 readiness-flow tests: never touch Chrome, network or Send."""
import pytest

from ai_commerce.remote import chrome_acquisition
from ai_commerce.remote.wake_readiness_flow import (
    ReadinessFlow, _unique_json,
)


class FakePanel:
    def __init__(self):
        self.state = {"phase": "READY", "source_sha": "a" * 40}
        self.binding = "enrolled-route-9333"
        self.chat_code = "EXACT_CHAT_PRESENT"
        self.open_code = "EXACT_CHAT_OPENED"
        self.open_calls = 0
        self.inspections = 0

    def _state(self):
        return self.state.copy() if self.state is not None else None

    def _route(self):
        return self.binding

    def status(self):
        return {
            "phase": self.state["phase"],
            "route": "ENROLLED" if self.binding else "UNAVAILABLE",
            "tcp": "REACHABLE_UNVERIFIED",
        }

    def inspect_exact_chat(self):
        self.inspections += 1
        return {"code": self.chat_code}

    def open_exact_chat(self):
        self.open_calls += 1
        if self.open_code in ("EXACT_CHAT_OPENED", "EXACT_CHAT_PRESENT"):
            self.chat_code = "EXACT_CHAT_PRESENT"
        return {"code": self.open_code}


class FakeChrome:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result or {
            "status": "READY_ATTACH", "cdp_verified": True, "launched": False,
        }
        self.error = error

    def __call__(self, opt_in_launch):
        self.calls.append(opt_in_launch)
        if self.error is not None:
            raise chrome_acquisition.AcquisitionBlocked(self.error)
        return self.result


def flow(panel=None, chrome=None, sha=None):
    panel = panel or FakePanel()
    chrome = chrome or FakeChrome()
    value = sha if sha is not None else "a" * 40
    return ReadinessFlow(
        panel=panel, acquire=chrome, source_reader=lambda: value
    ), panel, chrome


def test_normal_ready_reuses_existing_owned_chrome_without_launch_or_send():
    f, p, c = flow()
    result = f.execute(opt_in_launch=False, opt_in_open_chat=False)
    assert result["status"] == "READY_FOR_PREPARE"
    assert result["chrome"] == "VERIFIED_9333"
    assert result["chat"] == "EXACT_CHAT_PRESENT"
    assert result["wake_sent"] is False
    assert result["host_plugin_invocation_proven"] is False
    assert c.calls == [False]
    assert p.open_calls == 0


@pytest.mark.parametrize("phase,code", [
    ("SUBMISSION_UNKNOWN", "SUBMISSION_UNKNOWN_NO_RETRY"),
    ("COMPLETE_UI_ONLY", "LEDGER_NOT_READY"),
])
def test_invalid_ledger_blocks_before_browser(phase, code):
    p = FakePanel()
    p.state["phase"] = phase
    f, p, c = flow(panel=p)
    assert f.execute(opt_in_launch=True, opt_in_open_chat=True)["code"] == code
    assert c.calls == []


def test_missing_ledger_or_route_or_sha_blocks_before_browser():
    for condition, expected in (
        ("ledger", "LEDGER_UNAVAILABLE"),
        ("route", "ROUTE_NOT_ENROLLED_9333"),
        ("sha", "SOURCE_SHA_CHANGED"),
    ):
        p = FakePanel()
        if condition == "ledger":
            p.state = None
        if condition == "route":
            p.binding = None
        f, _, c = flow(panel=p, sha="b" * 40 if condition == "sha" else None)
        assert f.execute(opt_in_launch=True, opt_in_open_chat=True)["code"] == expected
        assert c.calls == []


def test_opt_in_is_forwarded_once_and_no_listener_stops():
    c = FakeChrome(result={
        "status": "NOT_RUNNING", "cdp_verified": False, "launched": False,
    })
    f, p, _ = flow(chrome=c)
    assert f.execute(opt_in_launch=False, opt_in_open_chat=True)["code"] == "CHROME_NOT_RUNNING"
    assert c.calls == [False]
    assert p.open_calls == 0


def test_failed_launch_blocks_no_retries_even_when_profile_may_be_active():
    c = FakeChrome(error="CDP_NOT_LOOPBACK_OWNED")
    f, p, _ = flow(chrome=c)
    r = f.execute(opt_in_launch=True, opt_in_open_chat=True)
    assert r["code"] == "CHROME_CDP_NOT_LOOPBACK_OWNED"
    assert c.calls == [True]
    assert p.inspections == 0
    assert p.open_calls == 0


def test_exact_chat_not_open_without_consent_never_navigates():
    p = FakePanel()
    p.chat_code = "EXACT_CHAT_NOT_OPEN"
    f, p, c = flow(panel=p)
    r = f.execute(opt_in_launch=True, opt_in_open_chat=False)
    assert r["status"] == "BLOCKED"
    assert r["code"] == "EXACT_CHAT_EXACT_CHAT_NOT_OPEN"
    assert p.open_calls == 0


def test_exact_chat_open_is_bounded_one_operator_authorized_preload():
    p = FakePanel()
    p.chat_code = "EXACT_CHAT_NOT_OPEN"
    c = FakeChrome(result={
        "status": "READY_ATTACH", "cdp_verified": True, "launched": True,
    })
    f, _, _ = flow(panel=p, chrome=c)
    r = f.execute(opt_in_launch=True, opt_in_open_chat=True)
    assert r["status"] == "READY_FOR_PREPARE"
    assert r["launched"] is True
    assert p.open_calls == 1
    assert p.inspections == 2
    assert c.calls == [True]


def test_ambiguous_preload_blocks_without_retries_or_send():
    p = FakePanel()
    p.chat_code = "EXACT_CHAT_NOT_OPEN"
    p.open_code = "PRELOAD_NAVIGATION_UNKNOWN"
    f, _, c = flow(panel=p)
    r = f.execute(opt_in_launch=True, opt_in_open_chat=True)
    assert r["code"] == "EXACT_CHAT_PRELOAD_PRELOAD_NAVIGATION_UNKNOWN"
    assert r["wake_sent"] is False
    assert p.open_calls == 1
    assert p.inspections == 1
    assert c.calls == [True]


def test_source_drift_after_chat_read_blocks_ready():
    p = FakePanel()
    seen = iter(["a" * 40, "b" * 40])
    f = ReadinessFlow(
        panel=p, acquire=FakeChrome(), source_reader=lambda: next(seen)
    )
    r = f.execute(opt_in_launch=True, opt_in_open_chat=False)
    assert r["code"] == "SOURCE_SHA_CHANGED"


def test_reject_duplicate_private_manifest_json_fields():
    with pytest.raises(ValueError, match="DUPLICATE_KEY"):
        _unique_json([("version", 1), ("version", 1)])
