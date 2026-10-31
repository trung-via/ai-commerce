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



def test_real_guard_localized_send_dom_requires_unique_eligible_primary_button():
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
    from ai_commerce.remote.cdp_doorbell import _READY, _ARGS, _doorbell_text

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    program = r"""
const fs=require('node:fs');
const obj=JSON.parse(fs.readFileSync(0,'utf8'));
const input=obj.text;
const rows=input.split('\n');
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
         testid:opts.testid||null,primary:opts.primary!==false,
         disabled:opts.disabled||false,
         getClientRects:()=>[1],getAttribute:k=>
           k==='aria-disabled'?(opts.ariaDisabled?'true':null):null};
}
const b=()=>button('Gửi tin nhắn');
if(check({buttons:[b()]})!==true) process.exit(21);
if(check({buttons:[button('Gửi tệp',{primary:false})]})!==false) process.exit(22);
if(check({buttons:[button('Send', {primary:true})]})!==false) process.exit(23);
if(check({buttons:[button('Gửi tin nhắn',{disabled:true})]})!==false) process.exit(24);
if(check({buttons:[button('Gửi tin nhắn',{ariaDisabled:true})]})!==false) process.exit(25);
if(check({buttons:[b(),b()]})!==false) process.exit(26);
if(check({buttons:[b()],outside:true})!==false) process.exit(27);
if(check({buttons:[b()],generation:true})!==false) process.exit(28);
if(check({buttons:[b()],wrongText:true})!==false) process.exit(29);
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({
            "ready": _READY,
            "args": {**_ARGS, "url": URL, "text": _doorbell_text(EVENT, "trung-via/ai-commerce")},
            "text": _doorbell_text(EVENT, "trung-via/ai-commerce"),
        }),
        text=True, capture_output=True, timeout=45 if sys.platform == "win32" else 12,
    )
    assert result.returncode == 0, (
        f"Localized D4 DOM readiness failed (exit={result.returncode}); "
        f"stderr={result.stderr[:1000]}"
    )
