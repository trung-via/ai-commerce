"""Opt-in Chrome CDP doorbell for an already-authenticated, exact ChatGPT chat.

Adapted as an independent AI Commerce component after reading the user's
AIOS-Renew local_chat_wake.py source. No AIOS import, credentials, session data,
browser launching, navigation, assistant scraping or automatic activation.

A witnessed browser user turn is only *UI evidence*, not an authenticated
ChatGPT server receipt or proof of autonomous Brain continuation. D2 therefore
continues to return UNKNOWN and holds the event against blind replay.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Protocol

from .conversation_binding import ResolvedBinding, _CHAT, _REPOSITORY, _endpoint
from .wake_adapter import WakeReceipt

_EVENT = re.compile(r"ef02-completion-v1:[0-9a-f]{64}\Z")
_COMPOSER = ('#prompt-textarea[contenteditable="true"], '
             'main form [contenteditable="true"][role="textbox"][aria-multiline="true"]')
_SEND = '[data-testid="send-button"], button[type="submit"][aria-label="Send"]'
_STOP = '[data-testid="stop-button"]'
_LOGIN = '[data-testid="login-button"], a[href^="/auth/login"]'
_WORK = '[data-workspace-type="team"], [data-workspace-type="enterprise"], [data-workspace-type="business"], [data-testid="work-composer"]'

# All page-side scripts return bounded status codes, never transcript contents,
# cookies, account data or ChatGPT response text.
_GUARD = """
const visible = sel => [...document.querySelectorAll(sel)].filter(
 e => e.getClientRects().length > 0 && getComputedStyle(e).visibility === 'visible');
const composerSel = args.composer, sendSel = args.send;
const equivalent = box => {
 if (box.innerText === args.text && box.textContent === args.text) return true;
 const lines=args.text.split('\\n'), nodes=[...box.childNodes];
 const block=node => node.nodeType===1 && ['P','DIV'].includes(node.tagName);
 if(nodes.length !== lines.length || !nodes.some(block)) return false;
 if(!nodes.every((node,i) => node.textContent === lines[i] &&
      (node.nodeType === 3 || (block(node) &&
       [...node.childNodes].every(child => child.nodeType === 3) &&
       node.getClientRects().length && getComputedStyle(node).visibility === 'visible'))))
   return false;
 let offset=0;
 for(let i=0;i<lines.length;i++){
   if(box.innerText.slice(offset,offset+lines[i].length)!==lines[i]) return false;
   offset+=lines[i].length;
   if(i+1<lines.length){
     if(!block(nodes[i]) && !block(nodes[i+1])) return false;
     for(let n=0;n<2 && box.innerText[offset]==='\\n';n++)offset++;
   }
 }
 return offset===box.innerText.length;
};
const matches = () => {
 const roles = visible('[data-message-author-role="user"]');
 const candidates = roles.length ? roles : visible('[data-user-message-bubble]');
 return candidates.filter(e => e.textContent.replace(/\\r\\n/g,'\\n') === args.text);
};
const inspect = (expectText = false) => {
 if (location.href.replace(/\\/$/,'') !== args.url) return 'TARGET_PAGE_CHANGED';
 if (visible('main').length !== 1 || visible(args.login).length ||
     visible(args.work).length) return 'SURFACE_UNPROVEN';
 const boxes = visible(composerSel);
 if (boxes.length !== 1 || boxes[0].getAttribute('aria-disabled') === 'true')
   return 'SURFACE_UNPROVEN';
 if (visible(args.stop).length) return 'GENERATION_ACTIVE';
 const box = boxes[0];
 const original = box.textContent || '';
 if (expectText ? !equivalent(box) : original !== '') return 'DRAFT_PRESENT';
 const forms = [];
 for(let p=box.parentElement;p;p=p.parentElement) if(p.tagName==='FORM') forms.push(p);
 if(forms.length !== 1) return 'SURFACE_UNPROVEN';
 const buttons = [...forms[0].querySelectorAll(sendSel)].filter(
  b => b.getClientRects().length && getComputedStyle(b).visibility === 'visible');
 if (buttons.length > 1) return 'SURFACE_UNPROVEN';
 return {box, buttons};
};
"""
_ARGS = {
    "composer": _COMPOSER, "send": _SEND, "stop": _STOP,
    "login": _LOGIN, "work": _WORK,
}

_CHECK = "(args) => { " + _GUARD + """
 const result = inspect();
 if (typeof result === 'string') return result;
 if (matches().length) return 'OUTBOUND_ALREADY_PRESENT';
 return 'READY';
}"""
_INSERT = "(args) => { " + _GUARD + """
 const result = inspect();
 if (typeof result === 'string' || matches().length) return false;
 const box=result.box;
 box.focus();
 if (document.activeElement !== box || box.textContent !== '') return false;
 const select=window.getSelection(), range=document.createRange();
 if(!select) return false;
 range.selectNodeContents(box); range.collapse(true);
 select.removeAllRanges(); select.addRange(range);
 if(!document.execCommand('insertText',false,args.text)) return false;
 if(!equivalent(box)) return false;
 box.dispatchEvent(new InputEvent('input', {
   bubbles:true, composed:true, inputType:'insertText', data:args.text
 }));
 return equivalent(box);
}"""
_READY = "(args) => { " + _GUARD + """
 const result=inspect(true);
 if(typeof result === 'string' || matches().length) return false;
 return result.buttons.length === 1 && !result.buttons[0].disabled &&
   result.buttons[0].getAttribute('aria-disabled') !== 'true';
}"""
_CLICK = "(args) => { " + _GUARD + """
 const result=inspect(true);
 if(typeof result === 'string' || matches().length ||
    result.buttons.length !== 1) return false;
 const button=result.buttons[0];
 if(button.disabled || button.getAttribute('aria-disabled') === 'true') return false;
 button.click(); return true;
}"""
_PROVE = "(args) => { " + _GUARD + """
 if (location.href.replace(/\\/$/,'') !== args.url ||
     visible('main').length !== 1 || visible(args.login).length ||
     visible(args.work).length) return false;
 const found=matches();
 return found.length===1 && found[0].getClientRects().length > 0;
}"""


class BrowserInventory(Protocol):
    def pages(self, endpoint: str): ...


class _PlaywrightInventory:
    @contextmanager
    def pages(self, endpoint: str):
        # Deliberately attach-only. Exiting Playwright disconnects the CDP
        # client, not the Human's Chrome tab. No navigate, launch or new_page.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=10000)
            yield [page for context in browser.contexts for page in context.pages]


class LocalChromeDoorbellAdapter:
    """UI-only exact-chat doorbell, explicitly opt-in, without semantic authority."""

    def __init__(self, *, repository: str = "trung-via/ai-commerce",
                 allow_submit: bool = False,
                 inventory: BrowserInventory | None = None):
        if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
            raise ValueError("DOORBELL_REPOSITORY_INVALID")
        self.repository = repository
        self.allow_submit = allow_submit is True
        self.inventory = inventory or _PlaywrightInventory()

    def send_wake(self, *, binding: ResolvedBinding,
                  message: str, event_id: str) -> WakeReceipt:
        # The D2-provided status summary is not semantic authority. Submit only
        # an independently constructed, minimal selector-only doorbell.
        def receipt(status: str, code: str) -> WakeReceipt:
            safe_route = binding.route_id if type(binding) is ResolvedBinding else "UNKNOWN"
            safe_generation = binding.generation if type(binding) is ResolvedBinding else 0
            return WakeReceipt(event_id=event_id, route_id=safe_route,
                               generation=safe_generation,
                               delivery_status=status, error_code=code)

        if (type(binding) is not ResolvedBinding or
                not isinstance(binding.chat_url, str) or not _CHAT.fullmatch(binding.chat_url) or
                not _endpoint(binding.cdp_endpoint) or
                not isinstance(event_id, str) or not _EVENT.fullmatch(event_id)):
            return receipt("UNSUPPORTED", "CHAT_URL_INVALID")
        if not self.allow_submit:
            return receipt("UNSUPPORTED", "CDP_PROBE_DISABLED")
        doorbell = ("[AI COMMERCE LOCAL CHAT WAKE]\\n"
                    + "event_id: " + event_id + "\\n"
                    + "repository: " + self.repository + "\\n"
                    + "fresh_brain_sync_required: true")
        args = {**_ARGS, "url": binding.chat_url, "text": doorbell}
        clicked = False
        staging_started = False
        try:
            with self.inventory.pages(binding.cdp_endpoint) as pages:
                matching = [page for page in pages
                            if isinstance(page.url, str) and
                            page.url.rstrip("/") == binding.chat_url]
                if len(matching) != 1:
                    return receipt("UNSUPPORTED", "EXACT_CHAT_TAB_NOT_FOUND" if not matching
                                   else "AMBIGUOUS_CHAT_TARGETS")
                page = matching[0]
                page.set_default_timeout(3000)
                result = page.evaluate(_CHECK, args)
                if result != "READY":
                    code = result if result in {
                        "TARGET_PAGE_CHANGED", "SURFACE_UNPROVEN", "GENERATION_ACTIVE",
                        "DRAFT_PRESENT", "OUTBOUND_ALREADY_PRESENT",
                    } else "SURFACE_UNPROVEN"
                    return receipt("UNSUPPORTED", code)
                if page.url.rstrip("/") != binding.chat_url:
                    return receipt("UNSUPPORTED", "TARGET_PAGE_CHANGED")
                staging_started = True
                if page.evaluate(_INSERT, args) is not True:
                    return receipt("UNKNOWN", "INSERT_BLOCKED")
                try:
                    page.wait_for_function(_READY, arg=args, timeout=3000)
                except Exception:
                    return receipt("UNKNOWN", "INSERT_BLOCKED")
                if page.url.rstrip("/") != binding.chat_url or page.evaluate(_READY, args) is not True:
                    return receipt("UNKNOWN", "SEND_BLOCKED")
                # D2 already persisted a write-ahead UNKNOWN intent. The
                # subsequent click is an irreversibly ambiguous boundary.
                clicked = True
                if page.evaluate(_CLICK, args) is not True:
                    return receipt("UNKNOWN", "SEND_BLOCKED")
                try:
                    page.wait_for_function(_PROVE, arg=args, timeout=5000)
                except Exception:
                    return receipt("UNKNOWN", "SUBMISSION_UNPROVEN")
                if page.url.rstrip("/") != binding.chat_url or page.evaluate(_PROVE, args) is not True:
                    return receipt("UNKNOWN", "SUBMISSION_UNPROVEN")
                # Only visible user-turn proof on the exact UI, not server ACK.
                return receipt("UNKNOWN", "UI_USER_TURN_OBSERVED")
        except ImportError:
            return receipt("UNSUPPORTED", "CDP_UNAVAILABLE")
        except Exception:
            # Treat a partially staged/clicked or transport-lost attempt as
            # uncertain: never auto-retry or assert ChatGPT confirmed delivery.
            return receipt("UNKNOWN" if staging_started or clicked else "UNSUPPORTED",
                           "SUBMISSION_UNPROVEN" if clicked else (
                               "INSERT_BLOCKED" if staging_started else "CDP_UNAVAILABLE"))
