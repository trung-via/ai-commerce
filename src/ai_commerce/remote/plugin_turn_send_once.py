"""Explicit D5 one-shot Send from an already-prepared exact-chat composer.

DISABLED by default. No route enrollment, navigation, auto-staging, Wake, retry,
GitHub evidence publication, or autonomous plugin orchestration. A successful
click is an UNKNOWN delivery outcome, never a ChatGPT host/server ACK.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from .conversation_binding import ResolvedBinding, _CHAT, _endpoint
from .plugin_selection_probe import (
    BRAIN, ENGINEERING, _COMPOSER, _LOGIN, _STOP, _WORK,
)
from .cdp_doorbell import _SEND
from .plugin_turn_handoff import TurnHandoffError, preview_next_turn, validate_session

_ARGS = {
    "composer": _COMPOSER, "login": _LOGIN, "work": _WORK,
    "stop": _STOP, "send": _SEND,
}

# The same guard is executed both read-only before persisting intent and
# synchronously with the single irreversible click. Nothing returns draft text.
_GUARD = r"""
const visible = e => e && e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility === 'visible';
const matching = sel => [...document.querySelectorAll(sel)].filter(visible);
const reject = code => ({code:code, button:null});
if (location.href.replace(/\/$/, '') !== args.url) return reject('TARGET_PAGE_CHANGED');
if (matching('main').length !== 1 || matching(args.login).length ||
    matching(args.work).length) return reject('SURFACE_UNPROVEN');
if (matching(args.stop).length) return reject('GENERATION_ACTIVE');
const editors = matching(args.composer);
if (editors.length !== 1) return reject('SURFACE_UNPROVEN');
const editor=editors[0];
if (editor.getAttribute('aria-disabled') === 'true')
    return reject('SURFACE_UNPROVEN');
const forms=[];
for(let p=editor.parentElement;p;p=p.parentElement)
    if(p.tagName === 'FORM') forms.push(p);
if(forms.length !== 1) return reject('SURFACE_UNPROVEN');
const form=forms[0];
const nodes=[...form.querySelectorAll('*')];
if(nodes.length > 1200) return reject('SURFACE_UNPROVEN');
const norm=e => String(e.textContent || '').trim().replace(/\s+/g,' ');
const marked=e => {
    for(let p=e, depth=0;p && p!==form && depth<4;p=p.parentElement,depth++){
        const typ=String(p.getAttribute('data-type') || '');
        const tid=String(p.getAttribute('data-testid') || '');
        if(p.getAttribute('data-plugin-id') !== null ||
           p.getAttribute('data-mention-id') !== null ||
           /^(plugin|mention|plugin-mention)$/i.test(typ) ||
           /(plugin-mention|mention-chip)/i.test(tid) ||
           p.getAttribute('contenteditable') === 'false') return true;
    }
    return false;
};
const state=name => {
    const found=nodes.filter(e=>visible(e) && norm(e)===name &&
        ![...e.children].some(c=>norm(c)===name));
    if(found.length!==1) return false;
    return marked(found[0]);
};
if (!state(args.name)) return reject('CHIP_UNVERIFIED');
const other=args.name===args.brain?args.engineering:args.brain;
if (nodes.some(e=>visible(e) && norm(e)===other &&
        ![...e.children].some(c=>norm(c)===other)))
    return reject('OTHER_PLUGIN_PRESENT');
const clean=s=>String(s || '').replace(/\r\n?/g,'\n').replace(/\u00a0/g,' ').trim();
const text=clean(editor.innerText);
const candidate=[
    args.name+'\n'+args.prompt,
    args.name+' '+args.prompt,
    '@'+args.name+'\n'+args.prompt,
    '@'+args.name+' '+args.prompt,
];
if(!candidate.includes(text)) return reject('DRAFT_MISMATCH');
const send=[...form.querySelectorAll(args.send)].filter(visible);
if(send.length!==1 || send[0].disabled ||
    send[0].getAttribute('aria-disabled')==='true')
    return reject('SEND_BUTTON_UNPROVEN');
return {code:'READY', button:send[0]};
"""
# Return only a code over the CDP boundary, never a live DOM element.
_PRECHECK_JS = "(args) => { const guard = () => { " + _GUARD + " }; return guard().code; }"
_CLICK_JS = "(args) => { const guard = () => { " + _GUARD + """ };
    const result=guard();
    if(result.code!=='READY') return false;
    result.button.click(); return true;
}"""
_CODES = frozenset({
    "READY", "TARGET_PAGE_CHANGED", "SURFACE_UNPROVEN",
    "GENERATION_ACTIVE", "CHIP_UNVERIFIED", "OTHER_PLUGIN_PRESENT",
    "DRAFT_MISMATCH", "SEND_BUTTON_UNPROVEN",
})


def _is_windows() -> bool:
    return os.name == "nt"


def _binding_valid(binding: object) -> bool:
    return (type(binding) is ResolvedBinding and
            isinstance(binding.chat_url, str) and
            _CHAT.fullmatch(binding.chat_url) is not None and
            _endpoint(binding.cdp_endpoint))


@dataclass(frozen=True)
class OneShotSendResult:
    code: str
    phase: str = "NOT_STARTED"
    # Never call a button click proof of message delivery or plugin invocation.
    sent: bool | None = False
    host_selection_proven: bool = False


class ExplicitTurnOneShotSender:
    """One-shot prepared-composer delivery gate; disabled unless explicitly enabled.

    The trusted caller supplies separately authenticated current-main and
    pre-enrolled-route readers. These are NOT accepted from GitHub Issue text.
    All checks are fail closed, no browser mutation before durable UNKNOWN.
    """
    def __init__(self, *, enabled: bool = False, inventory=None):
        self.enabled = enabled is True
        self.inventory = inventory

    def send_once(self, *, store, expected_state: dict, binding: ResolvedBinding,
                  current_main_sha, current_binding,
                  operator_approved: bool = False) -> OneShotSendResult:
        if not self.enabled:
            return OneShotSendResult("SEND_DISABLED")
        if operator_approved is not True:
            return OneShotSendResult("OPERATOR_APPROVAL_REQUIRED")
        if not _is_windows():
            return OneShotSendResult("WINDOWS_INTERACTIVE_REQUIRED")
        if not _binding_valid(binding) or not callable(current_main_sha) or not callable(current_binding):
            return OneShotSendResult("SEND_CONFIG_INVALID")
        try:
            state = validate_session(expected_state)
            preview = preview_next_turn(state)
            role = preview["role"]
            name = preview["plugin_alias_for_operator_selection"]
            if name not in (BRAIN, ENGINEERING):
                return OneShotSendResult("ROLE_UNSUPPORTED")
            if store.read() != state:
                return OneShotSendResult("TURN_STATE_CHANGED")
            if current_main_sha() != state["source_sha"]:
                return OneShotSendResult("SOURCE_SHA_CHANGED")
            if current_binding() != binding:
                return OneShotSendResult("ROUTE_STATE_CHANGED")
        except (TurnHandoffError, ValueError, TypeError):
            return OneShotSendResult("TURN_NOT_READY")
        except Exception:
            return OneShotSendResult("PREFLIGHT_UNAVAILABLE")

        intent_boundary_entered = False
        args = {**_ARGS, "url": binding.chat_url, "name": name,
                "prompt": preview["prompt"], "brain": BRAIN,
                "engineering": ENGINEERING}
        try:
            from .cdp_doorbell import _PlaywrightInventory
            inventory = self.inventory or _PlaywrightInventory()
            with inventory.pages(binding.cdp_endpoint) as pages:
                matching = [p for p in pages if isinstance(p.url, str)
                            and p.url.rstrip("/") == binding.chat_url]
                if len(matching) != 1:
                    return OneShotSendResult("EXACT_CHAT_NOT_UNIQUE")
                page = matching[0]
                code = page.evaluate(_PRECHECK_JS, args)
                if type(code) is not str or code not in _CODES:
                    return OneShotSendResult("SURFACE_UNPROVEN")
                if code != "READY":
                    return OneShotSendResult(code)
                # Human confirmation may have become stale. Fail closed before
                # the durable boundary; no writes and no browser Send.
                if (store.read() != state or
                        current_main_sha() != state["source_sha"] or
                        current_binding() != binding):
                    return OneShotSendResult("PREFLIGHT_CHANGED")
                if page.url.rstrip("/") != binding.chat_url or page.evaluate(_PRECHECK_JS, args) != "READY":
                    return OneShotSendResult("COMPOSER_CHANGED")
                # Irreversible boundary: durably write UNKNOWN first.
                intent_boundary_entered = True
                store.record_intent_checked(expected_state=state, operator_approved=True)
                # From this point, any failure is UNKNOWN and MUST NOT retry.
                try:
                    clicked = page.evaluate(_CLICK_JS, args)
                except Exception:
                    return OneShotSendResult("SEND_OUTCOME_UNKNOWN", "SUBMISSION_UNKNOWN", None)
                return OneShotSendResult(
                    "SEND_OUTCOME_UNKNOWN" if clicked is True else "SEND_BLOCKED_UNKNOWN",
                    "SUBMISSION_UNKNOWN", None)
        except TurnHandoffError:
            if intent_boundary_entered:
                return OneShotSendResult("INTENT_STATUS_UNKNOWN", "UNKNOWN", None)
            return OneShotSendResult("TURN_STATE_CHANGED")
        except Exception:
            if intent_boundary_entered:
                return OneShotSendResult("INTENT_STATUS_UNKNOWN", "UNKNOWN", None)
            return OneShotSendResult("CDP_UNAVAILABLE")
