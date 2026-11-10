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
    assert result["auto_repaired"] == []
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
    assert r["auto_repaired"] == ["CHROME_LAUNCHED", "EXACT_CHAT_PRELOADED"]
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
    assert r["next_action"] == "RECONCILE_NO_RETRY"
    assert r["wake_sent"] is False
    assert p.open_calls == 1
    assert p.inspections == 1
    assert c.calls == [True]


def test_source_drift_after_chat_read_blocks_ready():
    p = FakePanel()
    seen = iter(["a" * 40, "b" * 40])
    f = ReadinessFlow(
        panel=p, acquire=FakeChrome(result={
            "status": "READY_ATTACH", "cdp_verified": True,
            "launched": True,
        }), source_reader=lambda: next(seen)
    )
    r = f.execute(opt_in_launch=True, opt_in_open_chat=False)
    assert r["code"] == "SOURCE_SHA_CHANGED"


def test_reject_duplicate_private_manifest_json_fields():
    with pytest.raises(ValueError, match="DUPLICATE_KEY"):
        _unique_json([("version", 1), ("version", 1)])



def test_warm_wake_reads_fresh_main_only_once():
    p = FakePanel()
    calls = []
    def fresh_main():
        calls.append(1)
        return "a" * 40
    f = ReadinessFlow(panel=p, acquire=FakeChrome(),
                      source_reader=fresh_main)
    r = f.execute(opt_in_launch=False, opt_in_open_chat=False)
    assert r["status"] == "READY_FOR_PREPARE"
    assert r["verification"] == "FULL_ACQUISITION"
    assert len(calls) == 1


def test_unsafe_chrome_profile_claim_is_reconciliation_only():
    f, p, c = flow(chrome=FakeChrome(error="PROFILE_ALREADY_ACTIVE_NO_CDP"))
    result = f.execute(opt_in_launch=True, opt_in_open_chat=True)
    assert result["status"] == "BLOCKED"
    assert result["next_action"] == "RECONCILE_NO_RETRY"
    assert c.calls == [True]
    assert p.open_calls == 0


# WH-04/06: the 9444 readiness+Control Center track is distinct from
# the legacy 9333 @plugin Prepare interface. All fixtures are offline.
def test_wh04_9444_reuses_owned_browser_and_exact_private_route_without_send():
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    p = FakePanel()
    p.binding = ResolvedBinding(
        route_id="enrolled", generation=4,
        chat_url="https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc",
        cdp_endpoint="http://127.0.0.1:9444")
    c = FakeChrome()
    f = ReadinessFlow(panel=p, acquire=c,
                      source_reader=lambda: "a" * 40, cdp_port=9444)
    result = f.execute(opt_in_launch=False, opt_in_open_chat=False)
    assert result["status"] == "READY_TO_PREPARE"
    assert result["chrome"] == "VERIFIED_9444"
    assert result["wake_sent"] is False
    assert result["auto_repaired"] == []
    assert c.calls == [False]
    assert p.open_calls == 0


def test_wh04_9444_never_treats_legacy_9333_route_as_enrolled():
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    p = FakePanel()
    p.binding = ResolvedBinding(
        route_id="old", generation=1,
        chat_url="https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc",
        cdp_endpoint="http://127.0.0.1:9333")
    c = FakeChrome()
    f = ReadinessFlow(panel=p, acquire=c,
                      source_reader=lambda: "a" * 40, cdp_port=9444)
    result = f.execute(opt_in_launch=True, opt_in_open_chat=True)
    assert result["code"] == "ROUTE_NOT_ENROLLED_9444"
    assert result["wake_sent"] is False
    assert c.calls == []  # deny BEFORE any Chrome interaction


def test_wh04_invalid_port_never_constructs_readiness_flow():
    with pytest.raises(ValueError, match="READINESS_CDP_PORT_INVALID"):
        ReadinessFlow(panel=FakePanel(), acquire=FakeChrome(),
                      source_reader=lambda: "a" * 40, cdp_port=True)
    with pytest.raises(ValueError, match="READINESS_CDP_PORT_INVALID"):
        ReadinessFlow(panel=FakePanel(), acquire=FakeChrome(),
                      source_reader=lambda: "a" * 40, cdp_port=9222)


def test_wh06_9444_panel_has_no_plugin_prepare_or_send_and_never_leaks_chat():
    from ai_commerce.remote.wake_control_center import WakePanel, _HTML_9444
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    binding = ResolvedBinding(
        route_id="secret-route", generation=4,
        chat_url="https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc",
        cdp_endpoint="http://127.0.0.1:9444")
    class Store:
        def read(self):
            return dict(phase="READY", source_sha="a" * 40, turn_index=0)
    p = WakePanel(store=Store(), route_reader=lambda: binding,
                  tcp_probe=lambda: True, cdp_port=9444)
    status = p.status()
    assert status["route"] == "ENROLLED"
    assert status["can_open"] is True
    assert status["can_prepare"] is False
    assert status["can_send"] is False
    assert status["next_action"] == "OPEN_EXACT_CHAT_NO_SEND"
    assert p.prepare_turn("PREPARE-D5-BRAIN-0") == {
        "code": "WH07_NO_PLUGIN_PREPARE", "sent": False}
    assert "id=\"open\"" in _HTML_9444
    assert "/api/prepare-turn" not in _HTML_9444
    assert "id=\"send\"" not in _HTML_9444
    assert binding.chat_url not in _HTML_9444
    assert binding.route_id not in _HTML_9444


@pytest.mark.parametrize("endpoint", [
    "http://127.0.0.1:9333", "http://localhost:9444",
    "http://0.0.0.0:9444", "http://127.0.0.1:9444/?x=1",
])
def test_wh06_9444_panel_rejects_nonexact_browser_route(endpoint):
    from ai_commerce.remote.wake_control_center import WakePanel
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    class Store:
        def read(self):
            return dict(phase="READY", source_sha="a" * 40, turn_index=0)
    binding = ResolvedBinding(
        route_id="route", generation=1,
        chat_url="https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc",
        cdp_endpoint=endpoint)
    p = WakePanel(store=Store(), route_reader=lambda: binding,
                  tcp_probe=lambda: True, cdp_port=9444)
    s = p.status()
    assert s["route"] == "UNAVAILABLE"
    assert s["can_open"] is False
    assert s["can_prepare"] is False
    assert s["can_send"] is False


def test_wh06_9444_readonly_inspection_requires_idle_safe_chat_surface():
    from ai_commerce.remote.wake_control_center import WakePanel
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    from contextlib import contextmanager
    binding = ResolvedBinding(
        route_id="route", generation=1,
        chat_url="https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc",
        cdp_endpoint="http://127.0.0.1:9444")
    class Store:
        def read(self):
            return dict(phase="READY", source_sha="a" * 40, turn_index=0)
    state = ["DRAFT_PRESENT"]
    class Page:
        url = binding.chat_url
        def evaluate(self, script):
            assert "CANDIDATE_READY" in script
            return state[0]
    class Inventory:
        @contextmanager
        def contexts(self, endpoint):
            from types import SimpleNamespace
            assert endpoint == binding.cdp_endpoint
            yield [SimpleNamespace(pages=[Page()])]
    panel = WakePanel(store=Store(), route_reader=lambda: binding,
                      tcp_probe=lambda: True, cdp_port=9444,
                      inventory_factory=Inventory)
    assert panel.inspect_exact_chat()["code"] == "DRAFT_PRESENT"
    state[0] = "SURFACE_UNPROVEN"
    assert panel.inspect_exact_chat()["code"] == "SURFACE_UNPROVEN"
    state[0] = "CANDIDATE_READY"
    assert panel.inspect_exact_chat()["code"] == "EXACT_CHAT_PRESENT"
    assert panel.status()["can_send"] is False


def test_wh06_preview_only_literal_never_grants_send_even_when_enrolled():
    from ai_commerce.remote.wake_control_center import WakePanel, _HTML_9444
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    binding = ResolvedBinding(
        route_id="private-route-A", generation=4,
        chat_url="https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc",
        cdp_endpoint="http://127.0.0.1:9444")
    class Store:
        def read(self):
            return dict(phase="READY", turn_index=0, source_sha="a"*40)
    p = WakePanel(store=Store(), route_reader=lambda: binding,
                  tcp_probe=lambda: True, cdp_port=9444)
    result = p.preview_wake()
    assert result == {
        "schema_version": "WakeHubWH06Preview.v1",
        "code": "PREVIEW_ONLY",
        "text": "kiểm tra",
        "route": "PRESENT_UNATTESTED",
        "origin_proven": False, "event_proven": False,
        "consent_proven": False, "send_authorized": False,
        "wake_sent": False, "effect": "NOT_ATTEMPTED",
    }
    assert binding.chat_url not in str(result)
    assert binding.route_id not in str(result)
    assert "id=\"preview\"" in _HTML_9444
    assert "id=\"dry-run\"" in _HTML_9444
    assert "/api/preview" in _HTML_9444
    assert "/api/dry-run" in _HTML_9444
    assert "id=\"send\"" not in _HTML_9444
    assert "/api/prepare-turn" not in _HTML_9444


@pytest.mark.parametrize("visible,expected", [
    ("CANDIDATE_READY", "SURFACE_CANDIDATE_ORIGIN_UNATTESTED"),
    ("DRAFT_PRESENT", "DRAFT_PRESENT"),
    ("SURFACE_UNPROVEN", "SURFACE_UNPROVEN"),
    (None, "EXACT_CHAT_NOT_OPEN"),
])
def test_wh06_dry_run_only_read_does_not_create_tab_or_send(visible, expected):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from ai_commerce.remote.wake_control_center import WakePanel
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    binding = ResolvedBinding(
        route_id="private-A", generation=2,
        chat_url="https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc",
        cdp_endpoint="http://127.0.0.1:9444")
    attempts=[]
    class Store:
        def read(self):
            attempts.append("read_state")
            return dict(phase="READY", turn_index=0, source_sha="a"*40)
    class Page:
        url = binding.chat_url
        def evaluate(self, script):
            attempts.append("evaluate_only")
            assert "CANDIDATE_READY" in script
            return visible
    class Inventory:
        @contextmanager
        def contexts(self, endpoint):
            attempts.append("contexts_read")
            assert endpoint == binding.cdp_endpoint
            yield [SimpleNamespace(pages=[Page()] if visible is not None else [])]
    p = WakePanel(store=Store(), route_reader=lambda: binding,
                  tcp_probe=lambda: True, source_reader=lambda: "a"*40,
                  inventory_factory=Inventory, cdp_port=9444)
    result = p.dry_run_wake()
    assert result["code"] == expected
    assert result["send_authorized"] is False
    assert result["origin_proven"] is False
    assert result["event_proven"] is False
    assert result["consent_proven"] is False
    assert result["effect"] == "NOT_ATTEMPTED"
    assert result["wake_sent"] is False
    assert binding.chat_url not in str(result)
    assert "contexts_read" in attempts
    assert not any(x in attempts for x in ("new_page", "goto", "click", "send"))


@pytest.mark.parametrize("phase,expected", [
    ("SUBMISSION_UNKNOWN", "HOLD_UNKNOWN_EFFECT_NO_RETRY"),
    ("COMPLETE_UI_ONLY", "LEDGER_NOT_READY"),
])
def test_wh06_dry_run_old_unknown_or_finished_ledger_never_touches_browser(phase, expected):
    from ai_commerce.remote.wake_control_center import WakePanel
    class Store:
        def read(self):
            return dict(phase=phase, turn_index=0, source_sha="a"*40)
    class Forbidden:
        def __init__(self):
            raise AssertionError("browser should never be consulted")
    p = WakePanel(store=Store(), route_reader=lambda: None, cdp_port=9444,
                  tcp_probe=lambda: (_ for _ in ()).throw(AssertionError("no tcp")),
                  inventory_factory=Forbidden)
    assert p.dry_run_wake()["code"] == expected


def test_wh06_dry_run_rejects_multiple_contexts_even_with_matching_chat():
    from contextlib import contextmanager
    from types import SimpleNamespace
    from ai_commerce.remote.wake_control_center import WakePanel
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    binding = ResolvedBinding(route_id="private-A", generation=2,
        chat_url="https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc",
        cdp_endpoint="http://127.0.0.1:9444")
    class Store:
        def read(self):
            return dict(phase="READY", turn_index=0, source_sha="a"*40)
    class BadPage:
        url = binding.chat_url
        def evaluate(self, script):
            raise AssertionError("ambiguous context may not be used")
    class Inventory:
        @contextmanager
        def contexts(self, endpoint):
            yield [SimpleNamespace(pages=[BadPage()]), SimpleNamespace(pages=[])]
    p = WakePanel(store=Store(), route_reader=lambda: binding,
                  tcp_probe=lambda: True, source_reader=lambda:"a"*40,
                  inventory_factory=Inventory, cdp_port=9444)
    assert p.inspect_exact_chat()["code"] == "PRELOAD_CONTEXT_AMBIGUOUS"
    assert p.dry_run_wake()["code"] == "PRELOAD_CONTEXT_AMBIGUOUS"
    assert p.status()["can_send"] is False


def test_wh06_dry_run_detects_source_drift_before_browser_access():
    from ai_commerce.remote.wake_control_center import WakePanel
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    binding = ResolvedBinding(route_id="private-A", generation=2,
        chat_url="https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc",
        cdp_endpoint="http://127.0.0.1:9444")
    class Store:
        def read(self):
            return dict(phase="READY", turn_index=0, source_sha="a"*40)
    class Forbidden:
        def __init__(self):
            raise AssertionError("source mismatch must stop before browser")
    p = WakePanel(store=Store(), route_reader=lambda: binding,
                  tcp_probe=lambda: True, source_reader=lambda:"b"*40,
                  inventory_factory=Forbidden, cdp_port=9444)
    assert p.dry_run_wake()["code"] == "SOURCE_SHA_CHANGED"


def test_wh06_legacy_9333_never_gets_wh07_preview_or_dry_run():
    from ai_commerce.remote.wake_control_center import WakePanel
    class Store:
        def read(self):
            return dict(phase="READY", turn_index=0, source_sha="a"*40)
    p = WakePanel(store=Store(), route_reader=lambda: None,
                  tcp_probe=lambda: False, cdp_port=9333)
    assert p.preview_wake()["code"] == "WH07_SCOPE_UNAVAILABLE"
    assert p.preview_wake()["text"] == ""
    assert p.dry_run_wake()["code"] == "WH07_SCOPE_UNAVAILABLE"
    assert p.dry_run_wake()["wake_sent"] is False
