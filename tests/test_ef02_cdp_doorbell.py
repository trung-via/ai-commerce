"""Deterministic adapter tests: no real Chrome/account/message is involved."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest

from ai_commerce.remote.cdp_doorbell import (
    LocalChromeDoorbellAdapter, LocalChromeObserverAdapter, LocalChromeBrowserObserver,
    _OBSERVER_CHECK, _SEARCH_MARKER,
)
from ai_commerce.remote.conversation_binding import ResolvedBinding
from ai_commerce.remote.trusted_origin import (
    OriginError, PrivateOriginSnapshot, RendezvousEmission,
    RendezvousHit, RendezvousObservation,
)

URL = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001"
OTHER = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000002"
EVENT = "ef02-completion-v1:" + "a" * 64
BIND = ResolvedBinding(
    route_id="route-A", generation=1, chat_url=URL,
    cdp_endpoint="http://127.0.0.1:9444",
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


def test_malformed_direct_binding_fails_closed_without_browser_access():
    inv = FakeInventory(FakePage())
    outcome = LocalChromeDoorbellAdapter(allow_submit=True, inventory=inv).send_wake(
        binding=None, message="untrusted", event_id=EVENT,
    )
    assert outcome.delivery_status == "UNSUPPORTED"
    assert outcome.error_code == "CHAT_URL_INVALID"
    assert inv.calls == []


def test_doorbell_payload_uses_real_newlines_not_literal_backslash_n():
    from ai_commerce.remote.cdp_doorbell import _doorbell_text
    payload = _doorbell_text(EVENT, "trung-via/ai-commerce")
    assert payload.splitlines() == [
        "[AI COMMERCE LOCAL CHAT WAKE]",
        "event_id: " + EVENT,
        "repository: trung-via/ai-commerce",
        "fresh_brain_sync_required: true",
    ]
    assert payload.count("\n") == 3
    assert r"\n" not in payload



def test_send_only_9444_one_click_witness_never_requests_brain_or_plugin_work():
    page = FakePage()
    adapter = LocalChromeDoorbellAdapter(
        allow_submit=True, inventory=FakeInventory(page), send_only=True)
    receipt = adapter.send_wake(
        binding=BIND, message="untrusted caller-supplied content", event_id=EVENT)
    assert receipt.delivery_status == "UNKNOWN"  # UI witness, not server ACK
    assert receipt.error_code == "UI_USER_TURN_OBSERVED"
    assert receipt.chat_delivery_proven is False
    assert receipt.brain_continuation_proven is False
    assert page.counts == {"insert": 1, "click": 1, "prove": 2}
    assert page.submitted_text.splitlines() == [
        "[AI COMMERCE LOCAL CHAT WAKE]",
        "event_id: " + EVENT,
        "repository: trung-via/ai-commerce",
        "delivery_scope: send_only",
        "fresh_brain_sync_required: false",
        "automation_followup_requested: false",
    ]
    assert "caller-supplied" not in page.submitted_text
    assert "@AI Commerce" not in page.submitted_text


def test_send_only_rejects_wrong_chat_without_send_or_partial_stage():
    other = FakePage(OTHER)
    inventory = FakeInventory(other)
    receipt = LocalChromeDoorbellAdapter(
        allow_submit=True, inventory=inventory, send_only=True).send_wake(
            binding=BIND, message="", event_id=EVENT)
    assert receipt.error_code == "EXACT_CHAT_TAB_NOT_FOUND"
    assert other.counts == {"insert": 0, "click": 0, "prove": 0}


def test_real_guard_accepts_nested_editor_span_but_rejects_unexpected_text():
    """Exercise the actual production JS guard with DOM-like nodes in Node.js."""
    import json
    import shutil
    import subprocess
    from ai_commerce.remote.cdp_doorbell import _GUARD, _doorbell_text

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed in this Python environment")
    program = r"""
const fs = require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const args={text:input.text, composer:'unused', send:'unused'};
const getComputedStyle=n=>({visibility:n.hidden?'hidden':'visible'});
const from=code=>new Function('args','box','document','getComputedStyle',
                             input.guard+'; return equivalent(box);');
const check=from(input.guard);
const txt=s=>({nodeType:3,textContent:s});
const span=(s,hidden=false)=>({nodeType:1,tagName:'SPAN',
  childNodes:[txt(s)],hidden,getClientRects:()=>[1]});
const p=s=>({nodeType:1,tagName:'P',childNodes:[span(s)],
  getClientRects:()=>[1]});
const rows=input.text.split('\n');
const box=xs=>({innerText:xs.join('\n'),textContent:xs.join(''),childNodes:xs.map(p)});
const good=box(rows);
if(check(args,good,{},getComputedStyle)!==true) process.exit(21);
const extra=box([...rows,'UNEXPECTED']);
if(check(args,extra,{},getComputedStyle)!==false) process.exit(22);
const missing=box(rows.slice(0,-1));
if(check(args,missing,{},getComputedStyle)!==false) process.exit(23);
const hidden=box(rows); hidden.childNodes[0].childNodes[0].hidden=true;
if(check(args,hidden,{},getComputedStyle)!==false) process.exit(24);
""";
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"guard": _GUARD, "text": _doorbell_text(EVENT, "trung-via/ai-commerce")}),
        # Windows hosted full-suite CI occasionally starts Node slowly under
        # load. Keep a bounded timeout; this guard test is not a latency test.
        text=True, capture_output=True, timeout=45 if __import__("sys").platform == "win32" else 12,
    )
    assert result.returncode == 0, (f"DOM guard JS regression failed (code {result.returncode}); "
                                    f"stderr={result.stderr[:1000]}")




def test_vietnamese_send_button_requires_primary_action_svg_and_form_scope():
    """#177 live DOM: Vietnamese 'Gửi…' button and SVG primary-action icon.

    A translated aria-label alone can describe unrelated controls, so both
    an explicit send-prefix AND the SVG primary-action marker are required.
    """
    from ai_commerce.remote.cdp_doorbell import _SEND, _GUARD, _READY, _CLICK

    assert 'button[aria-label^="Gửi"]:has(svg.icon-primary-action)' in _SEND
    assert 'button[aria-label^="Gửi"]' not in _SEND.split(', ')
    assert 'button[type="submit"][aria-label="Send"]' in _SEND
    assert '[data-testid="send-button"]' in _SEND
    # The CSS selector is used only within the exact composer ancestor form,
    # not against all buttons across the ChatGPT page.
    assert 'forms[0].querySelectorAll(sendSel)' in _GUARD
    assert 'buttons.length > 1' in _GUARD
    assert 'result.buttons.length === 1' in _READY
    assert 'result.buttons.length !== 1' in _CLICK
    assert "button.click()" in _CLICK



def test_real_guard_localized_send_dom_requires_exact_fresh_event_and_unique_button(monkeypatch):
    """Run the real D4 readiness guard on a bounded, DOM-like Node fixture.

    The fixture models four P rows from the observed Vietnamese ChatGPT editor
    and evaluates the actual production _READY function. Candidate selection
    uses a limited CSS matcher for the explicitly supported _SEND patterns.
    No Chrome instance, real page or click is involved.
    """
    import json
    import shutil
    import subprocess
    import sys
    from unittest.mock import Mock
    from ai_commerce.remote.cdp_doorbell import _READY, _ARGS, _doorbell_text

    # Synthetic IDs only: this regression does not reuse any historical event.
    fresh_event = "ef02-completion-v1:" + "f" * 64
    stale_event = "ef02-completion-v1:" + "e" * 64
    send_wake = Mock(side_effect=AssertionError("production send forbidden"))
    monkeypatch.setattr(LocalChromeDoorbellAdapter, "send_wake", send_wake)
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    program = r"""
const fs=require('node:fs');
const obj=JSON.parse(fs.readFileSync(0,'utf8'));
const input=obj.text;
let clicks=0;
const element=(name)=>({
  nodeType:1, tagName:name, getClientRects:()=>[1],
  getAttribute:()=>null, textContent:'', childNodes:[]
});
const style=()=>({visibility:'visible'});
globalThis.getComputedStyle=style;
globalThis.location={href:obj.args.url};
const live=eval(obj.ready);
function sendMatches(sel,b) {
  // Purpose-built matcher for supported selectors, not a generic CSS parser.
  // Ensures the production selector itself is routed through the form query.
  return sel.split(',').some(raw=>{
    const s=raw.trim();
    if(s==='[data-testid="send-button"]') return b.testid==='send-button';
    if(s==='button[type="submit"][aria-label="Send"]')
      return b.tagName==='BUTTON' && b.type==='submit' && b.label==='Send';
    if(s==='button[aria-label^="Gửi"]:has(svg.icon-primary-action)')
      return b.tagName==='BUTTON' && b.label.startsWith('Gửi') && b.primary;
    throw Error('UNTESTED_SEND_SELECTOR:'+s);
  });
}
function check(o) {
  const rows=(o.text ?? input).split('\n');
  const f=element('FORM');
  f.querySelectorAll=sel => o.outside ? [] :
    o.buttons.filter(b=>sendMatches(sel,b));
  const editor=element('DIV');
  editor.parentElement=f;
  editor.getAttribute=key=>key==='aria-disabled'?'false':null;
  editor.childNodes=rows.map(row=>{
    const p=element('P');
    p.childNodes=[{nodeType:3,textContent:row}];
    return p;
  });
  editor.textContent=rows.join('');
  editor.innerText=rows.join('\n\n');
  if(o.wrongText) editor.childNodes[0].childNodes[0].textContent+='EXTRA';
  const main=element('MAIN');
  globalThis.document={querySelectorAll:(sel)=>{
    if(sel==='main') return [main];
    if(sel===obj.args.composer) return [editor];
    if(sel===obj.args.stop) return o.generation ? [element('BUTTON')] : [];
    return [];
  }};
  return live(obj.args);
}
function button(label,opts={}) {
 return {tagName:'BUTTON',label,type:opts.type||'button',
         click:()=>{clicks++; throw Error('click forbidden in readiness regression');},
         testid:opts.testid||null,primary:opts.primary!==false,
         disabled:opts.disabled||false,
         getClientRects:()=>[1],getAttribute:k=>
           k==='aria-disabled'?(opts.ariaDisabled?'true':null):null};
}
const b=()=>button('Gửi tin nhắn');
// A fully formed draft for another event must not satisfy fresh-event readiness.
if(check({buttons:[b()],text:obj.staleText})!==false) process.exit(30);
if(check({buttons:[b()]})!==true) process.exit(21);
if(check({buttons:[button('Gửi tệp',{primary:false})]})!==false) process.exit(22);
if(check({buttons:[button('Send', {primary:true})]})!==false) process.exit(23);
if(check({buttons:[button('Gửi tin nhắn',{disabled:true})]})!==false) process.exit(24);
if(check({buttons:[button('Gửi tin nhắn',{ariaDisabled:true})]})!==false) process.exit(25);
if(check({buttons:[b(),b()]})!==false) process.exit(26);
if(check({buttons:[b()],outside:true})!==false) process.exit(27);
if(check({buttons:[b()],generation:true})!==false) process.exit(28);
if(check({buttons:[b()],wrongText:true})!==false) process.exit(29);
if(clicks!==0) process.exit(31);
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({
            "ready": _READY,
            "args": {**_ARGS, "url": URL, "text": _doorbell_text(fresh_event, "trung-via/ai-commerce")},
            "text": _doorbell_text(fresh_event, "trung-via/ai-commerce"),
            "staleText": _doorbell_text(stale_event, "trung-via/ai-commerce"),
        }),
        text=True, capture_output=True, timeout=45 if sys.platform == "win32" else 12,
    )
    assert result.returncode == 0, (
        f"Localized D4 DOM readiness failed (exit={result.returncode}); "
        f"stderr={result.stderr[:1000]}"
    )
    send_wake.assert_not_called()


def test_dual_plugin_wake_preview_is_non_delivering_and_has_two_at_names():
    from ai_commerce.remote.wake_dual_plugin_preview import build_dual_plugin_wake_preview

    fresh = "ef02-completion-v1:" + "f" * 64  # synthetic; NEVER send
    payload = build_dual_plugin_wake_preview(fresh)
    assert payload.count("@AI Commerce Brain") == 1
    assert payload.count("@AI Commerce Engineering Workflow") == 1
    assert payload.count("event_id: " + fresh) == 1
    assert "repository: trung-via/ai-commerce" in payload
    assert "fresh_brain_sync_required: true" in payload
    assert "NOT SENT" in payload
    assert "separately authorized" in payload
    assert "Brain-native research" in payload
    assert "UNKNOWN event" in payload


@pytest.mark.parametrize("event,repository", [
    ("ef02-completion-v1:" + "g" * 64, "trung-via/ai-commerce"),
    ("ef02-completion-v1:" + "a" * 63, "trung-via/ai-commerce"),
    ("ef02-completion-v1:" + "a" * 64 + "EXTRA", "trung-via/ai-commerce"),
    ("ef02-completion-v1:" + "a" * 64, "other/repo"),
    (None, "trung-via/ai-commerce"),
])
def test_dual_plugin_wake_preview_rejects_untrusted_input(event, repository):
    from ai_commerce.remote.wake_dual_plugin_preview import build_dual_plugin_wake_preview

    with pytest.raises(ValueError, match="DUAL_PLUGIN_PREVIEW_INPUT_INVALID"):
        build_dual_plugin_wake_preview(event, repository)


def _make_dual_route_failure_event(action_statuses):
    from ai_commerce.remote.completion_event import build_completion_event

    actions = []
    for idx, status in enumerate(action_statuses, 1):
        action = {"action_id": f"test_action_{idx}", "attempt_id": f"test_attempt_{idx}", "status": status}
        if status == "CANDIDATE_READY":
            action["candidate_sha"] = "a" * 40
        actions.append(action)
    succeeded = sum(a["status"] == "CANDIDATE_READY" for a in actions)
    report = {
        "kind": "AI_COMMERCE_REMOTE_BATCH_RESULT",
        "version": 1,
        "batch_id": "synthetic_failure_test",
        "source_sha": "f" * 40,
        "status": "COMPLETE" if succeeded == len(actions) else "PARTIAL_OR_FAILED",
        "actions": actions,
        "total": len(actions),
        "succeeded": succeeded,
    }
    return build_completion_event(
        report, repository="trung-via/ai-commerce",
        run_id=123456, run_attempt=1, issue_number=987,
    )


def test_failure_preview_selects_only_engineering_without_brain():
    from ai_commerce.remote.wake_dual_plugin_preview import build_engineering_failure_wake_preview

    event = _make_dual_route_failure_event(["EXECUTOR_FAILED"])
    message = build_engineering_failure_wake_preview(event)
    assert message.count("@AI Commerce Engineering Workflow") == 1
    assert "@AI Commerce Brain" not in message
    assert "failed_action: test_action_1 attempt=test_attempt_1 status=EXECUTOR_FAILED" in message
    assert "fresh_brain_sync_required: false" in message
    assert "run_id: 123456" in message
    assert "issue_number: 987" in message
    assert "PREVIEW — NOT SENT" in message
    assert "explicit authorization" in message


def test_failure_preview_in_mixed_batch_includes_only_nonpass_attempt():
    from ai_commerce.remote.wake_dual_plugin_preview import build_engineering_failure_wake_preview

    event = _make_dual_route_failure_event(["CANDIDATE_READY", "VERIFICATION_FAILED"])
    message = build_engineering_failure_wake_preview(event)
    assert "failed_action: test_action_2 attempt=test_attempt_2 status=VERIFICATION_FAILED" in message
    assert "failed_action: test_action_1" not in message
    assert message.count("@AI Commerce Engineering Workflow") == 1
    assert "@AI Commerce Brain" not in message


def test_failure_preview_rejects_success_only_event():
    from ai_commerce.remote.wake_dual_plugin_preview import build_engineering_failure_wake_preview

    event = _make_dual_route_failure_event(["CANDIDATE_READY"])
    with pytest.raises(ValueError, match="ENGINEERING_FAILURE_PREVIEW_NOT_FAILED"):
        build_engineering_failure_wake_preview(event)


@pytest.mark.parametrize("tamper", ["repo", "event_id", "status", "not_event"])
def test_failure_preview_rejects_forged_or_invalid_events(tamper):
    from ai_commerce.remote.wake_dual_plugin_preview import build_engineering_failure_wake_preview

    event = _make_dual_route_failure_event(["EXECUTION_UNKNOWN"])
    if tamper == "repo":
        event["repository"] = "other/repo"
    elif tamper == "event_id":
        event["event_id"] = "ef02-completion-v1:" + "a" * 64
    elif tamper == "status":
        event["actions"][0]["status"] = "CANDIDATE_READY"
    else:
        event = {"untrusted": True}
    with pytest.raises(ValueError, match="ENGINEERING_FAILURE_PREVIEW_EVENT_INVALID"):
        build_engineering_failure_wake_preview(event)


def test_preload_before_wake_uses_enrolled_route_only(monkeypatch):
    # Synthetic Windows browser: never connects to a real Chrome session.
    from types import SimpleNamespace
    from ai_commerce.remote import cdp_doorbell as d4
    monkeypatch.setattr(d4, "_is_windows", lambda: True)
    existing = FakePage(OTHER)
    created = []
    class NewPage:
        def __init__(self):
            self.url = "about:blank"
        def goto(self, url, **kwargs):
            assert url == URL and kwargs == {
                "wait_until": "domcontentloaded", "timeout": 15000}
            self.url = url
    class Ctx:
        pages = [existing]
        def new_page(self):
            page = NewPage()
            created.append(page)
            self.pages.append(page)
            return page
    ctx = Ctx()
    inv = d4._PlaywrightInventory()
    @contextmanager
    def fake_browser(endpoint):
        # D4 and D5 use the same isolated 9444 endpoint.
        assert endpoint == "http://127.0.0.1:9444"
        yield SimpleNamespace(contexts=[ctx])
    monkeypatch.setattr(inv, "_browser", fake_browser)
    # This preload does not touch a browser or route outside the isolated 9444.
    binding = ResolvedBinding(route_id=BIND.route_id, generation=1,
                              chat_url=URL, cdp_endpoint="http://127.0.0.1:9444")
    assert inv.preload(binding, current_binding=lambda: binding) == "EXACT_CHAT_OPENED"
    assert len(created) == 1 and created[0].url == URL
    assert existing.url == OTHER
    assert inv.preload(binding) == "EXACT_CHAT_PRESENT"  # idempotent reuse
    wrong_port = ResolvedBinding(route_id=BIND.route_id, generation=1,
                                 chat_url=URL, cdp_endpoint="http://127.0.0.1:9222")
    assert inv.preload(wrong_port) == "PRELOAD_ROUTE_INVALID"


def test_preload_rejects_ambiguous_targets_and_contexts(monkeypatch):
    from types import SimpleNamespace
    from ai_commerce.remote import cdp_doorbell as d4
    monkeypatch.setattr(d4, "_is_windows", lambda: True)
    inv = d4._PlaywrightInventory()
    @contextmanager
    def two_targets(_endpoint):
        yield SimpleNamespace(contexts=[
            SimpleNamespace(pages=[FakePage(URL), FakePage(URL)])])
    monkeypatch.setattr(inv, "_browser", two_targets)
    binding = ResolvedBinding(route_id="enrolled", generation=1, chat_url=URL,
                              cdp_endpoint="http://127.0.0.1:9444")
    assert inv.preload(binding) == "PRELOAD_EXACT_CHAT_AMBIGUOUS"
    @contextmanager
    def two_contexts(_endpoint):
        yield SimpleNamespace(contexts=[
            SimpleNamespace(pages=[FakePage(OTHER)]),
            SimpleNamespace(pages=[])])
    monkeypatch.setattr(inv, "_browser", two_contexts)
    assert inv.preload(binding) == "PRELOAD_CONTEXT_AMBIGUOUS"
    assert inv.preload(binding, current_binding=lambda: BIND) == "PRELOAD_ROUTE_CHANGED"


def test_wake_preload_failure_never_stages_or_sends():
    inv = FakeInventory(FakePage())
    inv.preload = lambda binding: "PRELOAD_CDP_UNAVAILABLE"
    result = LocalChromeDoorbellAdapter(
        allow_submit=True, inventory=inv, preload_exact_chat=True).send_wake(
            binding=BIND, message="ignored", event_id=EVENT)
    assert result.error_code == "PRELOAD_CDP_UNAVAILABLE"
    assert result.delivery_status == "UNSUPPORTED"
    assert inv.calls == []


OBSERVER_MARKER = "wh03." + "1" * 64
OBSERVER_DIGEST = hashlib.sha256(OBSERVER_MARKER.encode("ascii")).hexdigest()
ORIGIN_A = PrivateOriginSnapshot(
    route_id="route-A", generation=1, chat_url=URL,
    cdp_endpoint="http://127.0.0.1:9444",
    profile_id="profile-A", account_id="account-A",
    session_id="session-A", login_id="login-A",
)
EMISSION_A = RendezvousEmission(
    request_id="req-1",
    marker=OBSERVER_MARKER,
    marker_digest=OBSERVER_DIGEST,
    origin=ORIGIN_A,
)


class FakeObserverPage:
    def __init__(self, url=URL, *, gate="READY", markers=None, fail_step=None,
                 profile_id="profile-A", account_id="account-A"):
        self.url = url
        self.gate = gate
        self.markers = list(markers or [])  # list of (marker, message_id)
        self.fail_step = fail_step
        self.profile_id = profile_id
        self.account_id = account_id
        self.mutations = []
        self.untrusted_marker_copies = 0

    def set_default_timeout(self, n):
        assert n == 3000

    def goto(self, *args, **kwargs):
        self.mutations.append("goto")
        raise AssertionError("mutation forbidden: goto called on observer page")

    def click(self, *args, **kwargs):
        self.mutations.append("click")
        raise AssertionError("mutation forbidden: click called on observer page")

    def fill(self, *args, **kwargs):
        self.mutations.append("fill")
        raise AssertionError("mutation forbidden: fill called on observer page")

    def type(self, *args, **kwargs):
        self.mutations.append("type")
        raise AssertionError("mutation forbidden: type called on observer page")

    def evaluate(self, script, args):
        if self.fail_step == "disconnect":
            raise OSError("synthetic CDP disconnect")
        if self.fail_step == "timeout":
            raise TimeoutError("synthetic page timeout")
        if "_SEARCH_MARKER" in script or "document.querySelectorAll('*')" in script or "marker" in script:
            marker = args["marker"]
            matches = [{"message_id": mid} for m, mid in self.markers if m == marker]
            untrusted = getattr(self, "untrusted_marker_copies", 0)
            total = len(matches) + untrusted
            ambiguous = total > 1
            return {"matches": matches, "total": total, "ambiguous": ambiguous}
        if "location.href.replace" in script:
            if self.fail_step == "inspect_fail":
                return "SURFACE_UNPROVEN"
            if self.profile_id is None:
                return "PROFILE_UNPROVEN"
            if args.get("profile_id") and self.profile_id != args["profile_id"]:
                return "PROFILE_MISMATCH"
            if self.account_id is None:
                return "ACCOUNT_UNPROVEN"
            if args.get("account_id") and self.account_id != args["account_id"]:
                return "ACCOUNT_MISMATCH"
            return self.gate
        raise AssertionError("unexpected script evaluated on observer page")


class FakeObserverInventory:
    def __init__(self, *pages, contexts=None):
        self._pages = list(pages)
        self._contexts = contexts
        self.calls = []

    @contextmanager
    def pages(self, endpoint):
        self.calls.append(endpoint)
        yield self._pages

    @contextmanager
    def contexts(self, endpoint):
        self.calls.append(endpoint)
        if self._contexts is not None:
            yield self._contexts
        else:
            yield [SimpleNamespace(pages=self._pages)]


def test_observer_positive_unique_marker_and_reprove():
    page = FakeObserverPage(URL, markers=[(OBSERVER_MARKER, "turn-1")])
    obs = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    snapshot = obs.capture_origin(request_id="req-1", binding=BIND)
    assert snapshot == ORIGIN_A

    observation = obs.search_and_reprove(EMISSION_A, nonce="n" * 32)
    assert observation.request_id == "req-1"
    assert observation.marker_digest == OBSERVER_DIGEST
    assert observation.nonce == "n" * 32
    assert len(observation.hits) == 1
    assert observation.hits[0].marker == OBSERVER_MARKER
    assert observation.hits[0].origin == ORIGIN_A
    assert observation.hits[0].message_id == "turn-1"
    assert observation.opened_origin == ORIGIN_A


def test_observer_closed_chat_unproven_coverage():
    page = FakeObserverPage(URL, markers=[(OBSERVER_MARKER, "turn-1")])
    # Enrolled domain has 2 chats, but only 1 is open in browser (closed Chat B)
    obs = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page), enrolled_origin=ORIGIN_A, enrolled_chats=(URL, OTHER)
    )
    obs.capture_origin(request_id="req-1", binding=BIND)
    with pytest.raises(OriginError, match="SEARCH_COVERAGE_UNPROVEN"):
        obs.search_and_reprove(EMISSION_A, nonce="n" * 32)


def test_observer_partial_search_domain_unproven():
    page = FakeObserverPage(URL, markers=[(OBSERVER_MARKER, "turn-1")])
    scope_witness = lambda urls, orig: len(urls) >= 2 and OTHER in urls
    obs = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page), enrolled_origin=ORIGIN_A, scope_witness=scope_witness
    )
    obs.capture_origin(request_id="req-1", binding=BIND)
    with pytest.raises(OriginError, match="SEARCH_COVERAGE_UNPROVEN"):
        obs.search_and_reprove(EMISSION_A, nonce="n" * 32)


def test_observer_missing_profile_account_attributes_fails_closed():
    page_no_prof = FakeObserverPage(URL, profile_id=None)
    obs1 = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page_no_prof), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    with pytest.raises(OriginError, match="PROFILE_UNPROVEN"):
        obs1.capture_origin(request_id="req-1", binding=BIND)

    page_no_acc = FakeObserverPage(URL, account_id=None)
    obs2 = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page_no_acc), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    with pytest.raises(OriginError, match="ACCOUNT_UNPROVEN"):
        obs2.capture_origin(request_id="req-1", binding=BIND)


def test_observer_tampered_identity_fails_closed():
    page_tampered = FakeObserverPage(URL, profile_id="tampered-profile")
    obs_t = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page_tampered), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    with pytest.raises(OriginError, match="PROFILE_MISMATCH"):
        obs_t.capture_origin(request_id="req-1", binding=BIND)

    obs_self = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page_tampered), profile_id="profile-A"
    )
    with pytest.raises(OriginError, match="UNVERIFIED_ORIGIN"):
        obs_self.capture_origin(request_id="req-1", binding=BIND)


def test_observer_stale_generation_and_wrong_route():
    page = FakeObserverPage(URL)
    obs = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    stale_bind = replace(BIND, generation=2)
    with pytest.raises(OriginError, match="RENDEZVOUS_ORIGIN_INVALID"):
        obs.capture_origin(request_id="req-1", binding=stale_bind)

    wrong_route = replace(BIND, route_id="other-route")
    with pytest.raises(OriginError, match="RENDEZVOUS_ORIGIN_INVALID"):
        obs.capture_origin(request_id="req-1", binding=wrong_route)


def test_observer_prior_capture_binding_required():
    page = FakeObserverPage(URL, markers=[(OBSERVER_MARKER, "turn-1")])
    obs = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    # Search without prior capture_origin for request_id
    with pytest.raises(OriginError, match="RENDEZVOUS_ORIGIN_MISMATCH"):
        obs.search_and_reprove(EMISSION_A, nonce="n" * 32)


def test_observer_nonce_replay_fails_closed():
    page = FakeObserverPage(URL, markers=[(OBSERVER_MARKER, "turn-1")])
    obs = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    obs.capture_origin(request_id="req-1", binding=BIND)
    obs.search_and_reprove(EMISSION_A, nonce="a" * 32)
    with pytest.raises(OriginError, match="RENDEZVOUS_REPLAY"):
        obs.search_and_reprove(EMISSION_A, nonce="a" * 32)


def test_observer_disconnect_and_timeout_failures():
    page_disc = FakeObserverPage(URL, fail_step="disconnect")
    obs1 = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page_disc), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    with pytest.raises(OriginError, match="SEARCH_COVERAGE_UNPROVEN"):
        obs1.capture_origin(request_id="req-1", binding=BIND)

    page_timeout = FakeObserverPage(URL, markers=[(OBSERVER_MARKER, "turn-1")])
    obs2 = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page_timeout), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    obs2.capture_origin(request_id="req-1", binding=BIND)
    page_timeout.fail_step = "timeout"
    with pytest.raises(OriginError, match="SEARCH_COVERAGE_UNPROVEN"):
        obs2.search_and_reprove(EMISSION_A, nonce="b" * 32)


def test_observer_zero_hits_and_two_user_hits():
    page_0 = FakeObserverPage(URL, markers=[])
    obs_0 = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page_0), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    obs_0.capture_origin(request_id="req-1", binding=BIND)
    obs_res0 = obs_0.search_and_reprove(EMISSION_A, nonce="c" * 32)
    assert len(obs_res0.hits) == 0

    page_2 = FakeObserverPage(URL, markers=[(OBSERVER_MARKER, "turn-1"), (OBSERVER_MARKER, "turn-2")])
    obs_2 = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page_2), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    obs_2.capture_origin(request_id="req-1", binding=BIND)
    with pytest.raises(OriginError, match="RENDEZVOUS_SEARCH_NOT_UNIQUE"):
        obs_2.search_and_reprove(EMISSION_A, nonce="d" * 32)


def test_observer_duplicate_untrusted_marker_copies_fail_closed():
    page_untrusted = FakeObserverPage(URL, markers=[(OBSERVER_MARKER, "turn-1")])
    page_untrusted.untrusted_marker_copies = 1
    obs = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page_untrusted), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    obs.capture_origin(request_id="req-1", binding=BIND)
    with pytest.raises(OriginError, match="RENDEZVOUS_SEARCH_NOT_UNIQUE"):
        obs.search_and_reprove(EMISSION_A, nonce="e" * 32)


def test_observer_native_attestation_fails_closed():
    page = FakeObserverPage(URL, markers=[(OBSERVER_MARKER, "turn-1")])
    obs = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,),
        native_authority=True
    )
    obs.capture_origin(request_id="req-1", binding=BIND)
    with pytest.raises(OriginError, match="SEARCH_COVERAGE_UNPROVEN"):
        obs.search_and_reprove(EMISSION_A, nonce="f" * 32)


def test_observer_typed_search_domain_witness_incomplete_fails_closed():
    from ai_commerce.remote.trusted_origin import CompleteSearchDomainWitness

    class IncompleteSearchWitness:
        def is_search_scope_complete(self, *, origin, observed_urls, nonce):
            return False  # Closed or unloaded chats in account were not searched

    page = FakeObserverPage(URL, markers=[(OBSERVER_MARKER, "turn-1")])
    obs = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page), enrolled_origin=ORIGIN_A,
        scope_witness=IncompleteSearchWitness()
    )
    obs.capture_origin(request_id="req-1", binding=BIND)
    with pytest.raises(OriginError, match="SEARCH_COVERAGE_UNPROVEN"):
        obs.search_and_reprove(EMISSION_A, nonce="w" * 32)


def test_observer_native_evidence_consumer_with_g3_status_only_fails_closed():
    from ai_commerce.remote.trusted_origin import (
        NativeOriginEvidence, VerifiedNativeOriginEvidenceConsumer,
    )

    consumer = VerifiedNativeOriginEvidenceConsumer(expected_source_sha="a" * 64)
    # Evidence has valid G3 OS identity, but no complete search domain witness
    evidence = NativeOriginEvidence(
        source_sha="a" * 64,
        route_id=BIND.route_id,
        generation=BIND.generation,
        nonce="test-nonce" + "0" * 22,
        service_sid="S-1-5-80-1234567890",
        client_sid="S-1-5-21-123-456-789-1001",
        session_id=1,
        logon_id="0000000000012345",
        desktop=r"WinSta0\Default",
        cdp_endpoint="http://127.0.0.1:9444",
        profile_id="testProfileId",
        account_id="testAccountId",
        issued_ms=1000000,
        expires_ms=1060000,
        search_witness=None,
    )
    eval_res = consumer.evaluate_evidence(
        evidence,
        expected_route_id=BIND.route_id,
        expected_generation=BIND.generation,
        expected_nonce="test-nonce" + "0" * 22,
        now_ms=1001000,
    )
    assert eval_res.status == "HOLD"
    assert eval_res.code == "SEARCH_COVERAGE_UNPROVEN"
    assert eval_res.live_authority is False
    assert eval_res.partial_status is not None
    assert eval_res.partial_status["os_identity"] == "VERIFIED_LIVE_UNLOCKED_OWNER"
    assert "CHATGPT_ACCOUNT_SESSION_AUTHORITY" in eval_res.missing_owners
    assert "COMPLETE_SEARCH_DOMAIN_WITNESS" in eval_res.missing_owners
    assert consumer.is_complete_origin_attested() is False


def test_observer_strict_no_side_effects():
    page = FakeObserverPage(URL, markers=[(OBSERVER_MARKER, "turn-1")])
    obs = LocalChromeObserverAdapter(
        inventory=FakeObserverInventory(page), enrolled_origin=ORIGIN_A, enrolled_chats=(URL,)
    )
    obs.capture_origin(request_id="req-1", binding=BIND)
    obs.search_and_reprove(EMISSION_A, nonce="side-effect-free" + "0" * 16)
    assert page.mutations == []


def test_observer_privacy_and_redaction():
    obs = LocalChromeObserverAdapter(enrolled_origin=ORIGIN_A)
    assert repr(obs) == "LocalChromeObserverAdapter(<private>)"


def test_observer_search_marker_dom_semantics_in_node():
    import json
    import shutil
    import subprocess
    import sys

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")

    program = r"""
const fs = require('node:fs');
const obj = JSON.parse(fs.readFileSync(0, 'utf8'));
const marker = obj.marker;
const search = eval(obj.code);

const element = (tag, attrs={}, text='', parent=null) => {
  const el = {
    nodeType: 1, tagName: tag,
    attrs: { ...attrs },
    getAttribute: k => el.attrs[k] ?? null,
    hasAttribute: k => k in el.attrs,
    setAttribute: (k, v) => { throw Error('Mutation forbidden: setAttribute'); },
    removeAttribute: (k) => { throw Error('Mutation forbidden: removeAttribute'); },
    parent,
    closest: function(sel) {
      const selectors = sel.split(',').map(s => s.trim());
      for (const s of selectors) {
        if (s.startsWith('[') && s.endsWith(']')) {
          const inner = s.slice(1, -1);
          if (inner.includes('=')) {
            const [k, val] = inner.split('=');
            const cleanVal = val.replace(/[\"']/g, '');
            if (this.getAttribute(k) === cleanVal) return this;
          } else {
            if (this.hasAttribute(inner)) return this;
          }
        } else if (this.tagName.toLowerCase() === s.toLowerCase()) {
          return this;
        }
      }
      return this.parent ? this.parent.closest(sel) : null;
    },
    textContent: text,
    children: [],
    childNodes: [],
    getClientRects: () => [1]
  };
  return el;
};

const freezeTree = (el) => {
  Object.freeze(el.attrs);
  Object.freeze(el.children);
  Object.freeze(el.childNodes);
  Object.freeze(el);
  return el;
};

globalThis.getComputedStyle = () => ({ visibility: 'visible', display: 'block' });

// 1. Positive: exact match in user message container with data-message-id
const userMsg = element('DIV', { 'data-message-author-role': 'user', 'data-message-id': 'turn-1' }, marker);
const p = element('P', {}, marker, userMsg);
userMsg.children = [p];
userMsg.childNodes = [p];
freezeTree(userMsg);
freezeTree(p);
globalThis.document = { querySelectorAll: () => [userMsg, p] };
let res = search({ marker });
if (res.matches.length !== 1 || res.matches[0].message_id !== 'turn-1' || res.total !== 1 || res.ambiguous !== false) process.exit(1);

// 1b. Positive: exact match in user message container with data-turn-id
const userMsgTurn = element('DIV', { 'data-message-author-role': 'user', 'data-turn-id': 'turn-2' }, marker);
const pTurn = element('P', {}, marker, userMsgTurn);
userMsgTurn.children = [pTurn];
userMsgTurn.childNodes = [pTurn];
freezeTree(userMsgTurn);
freezeTree(pTurn);
globalThis.document = { querySelectorAll: () => [userMsgTurn, pTurn] };
res = search({ marker });
if (res.matches.length !== 1 || res.matches[0].message_id !== 'turn-2' || res.total !== 1 || res.ambiguous !== false) process.exit(2);

// 2. Substring match rejected
const subMsg = element('DIV', { 'data-message-author-role': 'user', 'data-message-id': 'turn-1' }, 'prefix ' + marker + ' suffix');
freezeTree(subMsg);
document.querySelectorAll = () => [subMsg];
res = search({ marker });
if (res.matches.length !== 0 || res.total !== 0) process.exit(3);

// 3. Assistant message rejected
const asstMsg = element('DIV', { 'data-message-author-role': 'assistant', 'data-message-id': 'turn-1' }, marker);
const asstP = element('P', {}, marker, asstMsg);
asstMsg.children = [asstP];
asstMsg.childNodes = [asstP];
freezeTree(asstMsg);
freezeTree(asstP);
document.querySelectorAll = () => [asstMsg, asstP];
res = search({ marker });
if (res.matches.length !== 0 || res.total !== 1) process.exit(4);

// 4. Draft / Editor rejected
const form = element('FORM', {}, marker);
const editor = element('DIV', { 'contenteditable': 'true' }, marker, form);
form.children = [editor];
form.childNodes = [editor];
freezeTree(form);
freezeTree(editor);
document.querySelectorAll = () => [form, editor];
res = search({ marker });
if (res.matches.length !== 0 || res.total !== 1) process.exit(5);

// 5. Non-user widget (button / toolbar / nav) rejected
const btn = element('BUTTON', {}, marker);
freezeTree(btn);
document.querySelectorAll = () => [btn];
res = search({ marker });
if (res.matches.length !== 0 || res.total !== 1) process.exit(6);

const nav = element('NAV', {}, marker);
freezeTree(nav);
document.querySelectorAll = () => [nav];
res = search({ marker });
if (res.matches.length !== 0 || res.total !== 1) process.exit(7);

// 6. Missing message author rejected
const divNoAuthor = element('DIV', { 'data-message-id': 'turn-1' }, marker);
freezeTree(divNoAuthor);
document.querySelectorAll = () => [divNoAuthor];
res = search({ marker });
if (res.matches.length !== 0 || res.total !== 1) process.exit(8);

// 7. Generic HTML id without data-message-id / data-turn-id rejected
const genericIdMsg = element('DIV', { 'data-message-author-role': 'user', 'id': 'generic-turn-id' }, marker);
freezeTree(genericIdMsg);
document.querySelectorAll = () => [genericIdMsg];
res = search({ marker });
if (res.matches.length !== 0 || res.total !== 1) process.exit(9);

// 8. Hidden element rejected
const hidden = element('DIV', { 'data-message-author-role': 'user', 'data-message-id': 'turn-1' }, marker);
freezeTree(hidden);
globalThis.getComputedStyle = () => ({ visibility: 'hidden' });
document.querySelectorAll = () => [hidden];
res = search({ marker });
if (res.matches.length !== 0 || res.total !== 0) process.exit(10);
globalThis.getComputedStyle = () => ({ visibility: 'visible', display: 'block' });

// 9. Duplicate marker: two true user messages
const u1 = element('DIV', { 'data-message-author-role': 'user', 'data-message-id': 'turn-1' }, marker);
const u2 = element('DIV', { 'data-message-author-role': 'user', 'data-message-id': 'turn-2' }, marker);
freezeTree(u1);
freezeTree(u2);
document.querySelectorAll = () => [u1, u2];
res = search({ marker });
if (res.matches.length !== 2 || res.total !== 2 || res.ambiguous !== true) process.exit(11);

// 10. Non-user copy / duplicate marker: one user message + one copy in assistant
// Must count copied marker as ambiguity without fabricating fake message IDs
const uA = element('DIV', { 'data-message-author-role': 'user', 'data-message-id': 'turn-1' }, marker);
const ast = element('DIV', { 'data-message-author-role': 'assistant', 'data-message-id': 'turn-asst' }, marker);
freezeTree(uA);
freezeTree(ast);
document.querySelectorAll = () => [uA, ast];
res = search({ marker });
if (res.total !== 2 || res.ambiguous !== true || res.matches.length !== 1 || res.matches[0].message_id !== 'turn-1') process.exit(12);
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"code": _SEARCH_MARKER, "marker": OBSERVER_MARKER}),
        text=True, capture_output=True, timeout=45 if sys.platform == "win32" else 12,
    )
    assert result.returncode == 0, f"Node DOM search failed (exit={result.returncode}); stderr={result.stderr}"

