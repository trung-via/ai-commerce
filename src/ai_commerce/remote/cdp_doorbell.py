"""Opt-in Chrome CDP doorbell for an already-authenticated, exact ChatGPT chat.

Adapted as an independent AI Commerce component after reading the user's
AIOS-Renew local_chat_wake.py source. No AIOS import, credentials, session data,
browser launching, navigation, assistant scraping or automatic activation.

A witnessed browser user turn is only *UI evidence*, not an authenticated
ChatGPT server receipt or proof of autonomous Brain continuation. D2 therefore
continues to return UNKNOWN and holds the event against blind replay.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
import re
import secrets
import time
from contextlib import contextmanager
from urllib.parse import urlsplit
from typing import Callable, Protocol
from pathlib import Path

from .conversation_binding import ResolvedBinding, _CHAT, _REPOSITORY, _endpoint
from .trusted_origin import (
    CompleteSearchDomainWitness, OriginError, PrivateOriginSnapshot,
    LocalCallerAttestation, OriginAuthority,
    RendezvousEmission, RendezvousHit, RendezvousObservation,
    VerifiedNativeOriginEvidenceConsumer, _MARKER, _private_id, _valid_origin,
)
from .wake_adapter import WakeReceipt
from ._private_state import PrivateJsonState, PrivateStateError

_EVENT = re.compile(r"ef02-completion-v1:[0-9a-f]{64}\Z")
_COMPOSER = ('#prompt-textarea[contenteditable="true"], '
             'main form [contenteditable="true"][role="textbox"][aria-multiline="true"]')
# The observed Vietnamese ChatGPT send button has an aria-label beginning
# with "Gửi" and a child SVG.icon-primary-action, but no English "Send" label.
# Match BOTH attributes to avoid accepting unrelated localized actions. The
# page guard additionally scopes candidates to one exact-chat composer form,
# requires a single visible, enabled button and checks exact staged text.
_SEND = ('[data-testid="send-button"], '
         'button[type="submit"][aria-label="Send"], '
         'button[aria-label^="Gửi"]:has(svg.icon-primary-action)')
_STOP = '[data-testid="stop-button"]'
_LOGIN = '[data-testid="login-button"], a[href^="/auth/login"]'
_WORK = '[data-workspace-type="team"], [data-workspace-type="enterprise"], [data-workspace-type="business"], [data-testid="work-composer"]'

# All page-side scripts return bounded status codes, never transcript contents,
# cookies, account data or ChatGPT response text.
_GUARD = """
const visible = sel => [...document.querySelectorAll(sel)].filter(
 e => e.getClientRects().length > 0 && getComputedStyle(e).visibility === 'visible');
const composerSel = args.composer, sendSel = args.send;
const plain = node => {
 if(node.nodeType === 3) return node.textContent;
 if(node.nodeType !== 1) return null;
 if(node.tagName === 'BR') return '\\n';
 if(!['SPAN','EM','STRONG','B','I'].includes(node.tagName) ||
    !node.getClientRects().length ||
    getComputedStyle(node).visibility !== 'visible') return null;
 let combined='';
 for(const child of node.childNodes){
   const text=plain(child);
   if(text===null) return null;
   combined+=text;
 }
 return combined;
};
const equivalent = box => {
 const same = text => typeof text === 'string' &&
    text.replace(/\\r\\n/g,'\\n') === args.text;
 if(same(box.innerText) && same(box.textContent)) return true;
 const nodes=[...box.childNodes];
 if(!nodes.length) return false;
 if(nodes.length === 1 && nodes[0].nodeType === 3)
   return same(nodes[0].textContent);
 // ChatGPT's contenteditable can wrap each line in a P/DIV and can insert
 // inline SPAN elements. Reconstruct only visible, allowlisted DOM nodes
 // and compare the WHOLE exact string; never accept substrings or prefixes.
 if(!nodes.every(n => n.nodeType === 1 && ['P','DIV'].includes(n.tagName) &&
      n.getClientRects().length && getComputedStyle(n).visibility === 'visible'))
   return false;
 const lines=[];
 for(const p of nodes){
   let line='';
   for(const child of p.childNodes){
     const text=plain(child);
     if(text===null) return false;
     line+=text;
   }
   lines.push(line);
 }
 return same(lines.join('\\n'));
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

_OBSERVER_CHECK = "(args) => { " + _GUARD + """
 if (location.href.replace(/\\/$/,'') !== args.url) return 'TARGET_PAGE_CHANGED';
 if (visible('main').length !== 1 || visible(args.login).length ||
     visible(args.work).length) return 'SURFACE_UNPROVEN';
 const boxes = visible(composerSel);
 if (boxes.length !== 1 || boxes[0].getAttribute('aria-disabled') === 'true')
   return 'SURFACE_UNPROVEN';
 if (visible(args.stop).length) return 'GENERATION_ACTIVE';
 if (args.profile_id) {
   const p = document.querySelector('[data-profile-id]');
   if (!p || !p.getAttribute('data-profile-id')) return 'PROFILE_UNPROVEN';
   if (p.getAttribute('data-profile-id') !== args.profile_id) return 'PROFILE_MISMATCH';
 }
 if (args.account_id) {
   const a = document.querySelector('[data-account-id]');
   if (!a || !a.getAttribute('data-account-id')) return 'ACCOUNT_UNPROVEN';
   if (a.getAttribute('data-account-id') !== args.account_id) return 'ACCOUNT_MISMATCH';
 }
 return 'READY';
}"""

_SEARCH_MARKER = """(args) => {
  const marker = args.marker;
  const isVis = (el) => {
    if (!el || el.nodeType !== 1) return false;
    try {
      if (typeof getComputedStyle === 'function') {
        const s = getComputedStyle(el);
        if (!s || s.visibility === 'hidden' || s.display === 'none') return false;
      }
    } catch (e) {}
    if (typeof el.getClientRects === 'function') {
      return el.getClientRects().length > 0;
    }
    return true;
  };

  const isDraftOrWidget = (el) => {
    if (!el || typeof el.closest !== 'function') return false;
    return !!el.closest('[contenteditable="true"], textarea, input, [role="textbox"], form, nav, header, button, [role="toolbar"], [role="navigation"]');
  };

  const isAssistant = (el) => {
    if (!el || typeof el.closest !== 'function') return false;
    return !!el.closest('[data-message-author-role="assistant"]');
  };

  const getUserMessageContainer = (el) => {
    if (!el || typeof el.closest !== 'function') return null;
    return el.closest('[data-message-author-role="user"], [data-user-message-bubble]');
  };

  const getMessageId = (container) => {
    if (!container) return null;
    const msgEl = (typeof container.closest === 'function' ? (container.closest('[data-message-id], [data-turn-id]') || container) : container);
    let mid = null;
    if (typeof msgEl.getAttribute === 'function') {
      mid = msgEl.getAttribute('data-message-id') || msgEl.getAttribute('data-turn-id');
    }
    if (typeof mid === 'string' && /^[0-9a-zA-Z._-]{1,64}$/.test(mid)) {
      return mid;
    }
    return null;
  };

  const candidates = [...document.querySelectorAll('*')].filter(isVis);
  let totalOccurrences = 0;
  const trustworthyMatches = [];
  const seenContainers = new Set();

  for (const el of candidates) {
    const text = (el.textContent || '').replace(/\\r\\n/g, '\\n').trim();
    if (text === marker) {
      const children = [...(el.children || el.childNodes || [])].filter(c => c.nodeType === 1);
      const childMatch = children.some(c => (c.textContent || '').replace(/\\r\\n/g, '\\n').trim() === marker);
      if (childMatch) {
        continue;
      }
      totalOccurrences++;

      if (isDraftOrWidget(el)) {
        continue;
      }
      if (isAssistant(el)) {
        continue;
      }
      const container = getUserMessageContainer(el);
      if (!container || !isVis(container)) {
        continue;
      }
      const msgId = getMessageId(container);
      if (!msgId) {
        continue;
      }
      if (seenContainers.has(container)) {
        continue;
      }
      seenContainers.add(container);
      trustworthyMatches.push({ message_id: msgId });
    }
  }

  return {
    matches: trustworthyMatches,
    total: totalOccurrences,
    ambiguous: totalOccurrences > 1
  };
}"""



def _doorbell_text(event_id: str, repository: str, *, send_only: bool = False,
                   fixed_check_test: bool = False) -> str:
    """Bounded exact Wake text; opt-in test only sends literal 'kiểm tra'."""
    if fixed_check_test:
        if send_only is not True or repository != "trung-via/ai-commerce":
            raise ValueError("DOORBELL_TEST_SCOPE_INVALID")
        return "kiểm tra"
    lines = [
        "[AI COMMERCE LOCAL CHAT WAKE]",
        "event_id: " + event_id,
        "repository: " + repository,
    ]
    if send_only:
        lines.extend(("delivery_scope: send_only",
                      "fresh_brain_sync_required: false",
                      "automation_followup_requested: false"))
    else:
        # Historical text shape remains available for old read-only fixtures.
        lines.append("fresh_brain_sync_required: true")
    return "\n".join(lines)


def _is_windows() -> bool:
    return os.name == "nt"


class BrowserInventory(Protocol):
    def pages(self, endpoint: str): ...


class _PlaywrightInventory:
    @contextmanager
    def _browser(self, endpoint: str):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=10000)
            yield browser  # Disconnect the CDP client only, never close Chrome.

    @contextmanager
    def pages(self, endpoint: str):
        # Existing probes and Send remain attach-only.
        with self._browser(endpoint) as browser:
            yield [page for context in browser.contexts for page in context.pages]

    @contextmanager
    def contexts(self, endpoint: str):
        # Read only; separate contexts must not be confused as one account.
        with self._browser(endpoint) as browser:
            yield list(browser.contexts)

    def preload(self, binding: ResolvedBinding, *, current_binding=None) -> str:
        """Auto-load exactly the private, enrolled chat; no Send or tab hijack.

        Only used after the existing operator/D2 authorization. A unique Chrome
        context is needed to create a new tab, and redirects fail closed.
        """
        if (not _is_windows() or type(binding) is not ResolvedBinding or
                type(binding.chat_url) is not str or
                _CHAT.fullmatch(binding.chat_url) is None or
                not _endpoint(binding.cdp_endpoint) or
                urlsplit(binding.cdp_endpoint).port != 9444):
            return "PRELOAD_ROUTE_INVALID"
        if current_binding is not None and not callable(current_binding):
            return "PRELOAD_ROUTE_INVALID"
        try:
            if current_binding is not None and current_binding() != binding:
                return "PRELOAD_ROUTE_CHANGED"
            with self._browser(binding.cdp_endpoint) as browser:
                contexts = list(browser.contexts)
                pages = [page for ctx in contexts for page in ctx.pages]
                matches = [page for page in pages
                           if isinstance(page.url, str) and
                           page.url.rstrip("/") == binding.chat_url]
                if len(matches) > 1:
                    return "PRELOAD_EXACT_CHAT_AMBIGUOUS"
                if current_binding is not None and current_binding() != binding:
                    return "PRELOAD_ROUTE_CHANGED"
                if matches:
                    return "EXACT_CHAT_PRESENT"
                if len(contexts) != 1:
                    return "PRELOAD_CONTEXT_AMBIGUOUS"
                # Never navigate the currently active, latest, or other tab.
                new_page = contexts[0].new_page()
                try:
                    new_page.goto(binding.chat_url,
                                  wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    # It may have navigated despite timeout: no automatic retry.
                    return "PRELOAD_NAVIGATION_UNKNOWN"
                if new_page.url.rstrip("/") != binding.chat_url:
                    return "PRELOAD_NAVIGATION_UNVERIFIED"
                if current_binding is not None and current_binding() != binding:
                    return "PRELOAD_ROUTE_CHANGED"
                return "EXACT_CHAT_OPENED"
        except Exception:
            return "PRELOAD_CDP_UNAVAILABLE"



# This script returns an enum, not transcript, account/session, cookies or URL.
_CHAT_A_READ_ONLY_SURFACE = """
() => {
  const visible = sel => [...document.querySelectorAll(sel)].filter(el =>
    el.getClientRects().length > 0 && getComputedStyle(el).visibility === 'visible');
  if (location.origin !== 'https://chatgpt.com' || visible('main').length !== 1 ||
      visible('[data-testid="login-button"], a[href^="/auth/login"]').length ||
      visible('[data-workspace-type="team"], [data-workspace-type="enterprise"], [data-workspace-type="business"], [data-testid="work-composer"]').length ||
      visible('[data-testid="stop-button"]').length) return 'SURFACE_UNPROVEN';
  const editors = visible('#prompt-textarea[contenteditable="true"], main form [contenteditable="true"][role="textbox"][aria-multiline="true"]');
  if (editors.length !== 1 || editors[0].getAttribute('aria-disabled') === 'true')
    return 'SURFACE_UNPROVEN';
  const box = editors[0];
  if (box.textContent !== '') return 'DRAFT_PRESENT';
  const forms = [];
  for (let p = box.parentElement; p; p = p.parentElement)
    if (p.tagName === 'FORM') forms.push(p);
  return forms.length === 1 ? 'CANDIDATE_READY' : 'SURFACE_UNPROVEN';
}
"""


def probe_chat_a_candidate(*, endpoint: str = "http://127.0.0.1:9444",
                           inventory=None) -> str:
    """Bounded native read of one open ChatGPT tab, NOT authenticated Chat A.

    No Send, navigation, page creation, private URL output or live-origin grant.
    """
    if not _is_windows() or type(endpoint) is not str or endpoint != "http://127.0.0.1:9444":
        return "NATIVE_DESKTOP_UNPROVEN"
    source = inventory if inventory is not None else _PlaywrightInventory()
    if not callable(getattr(source, "contexts", None)):
        return "CONTEXT_UNPROVEN"
    try:
        with source.contexts(endpoint) as contexts:
            if len(contexts) != 1:
                return "CONTEXT_UNPROVEN"
            pages = list(contexts[0].pages)
            if len(pages) > 32:
                return "CONTEXT_UNPROVEN"
            matches = [p for p in pages
                       if isinstance(getattr(p, "url", None), str)
                       and _CHAT.fullmatch(p.url.rstrip("/")) is not None]
            if len(matches) != 1:
                return ("MULTIPLE_CHAT_CANDIDATES" if matches
                        else "CHAT_A_CANDIDATE_NOT_FOUND")
            result = matches[0].evaluate(_CHAT_A_READ_ONLY_SURFACE)
            if result == "CANDIDATE_READY":
                return "CHAT_A_CANDIDATE_PRESENT_UNATTESTED"
            return "DRAFT_PRESENT" if result == "DRAFT_PRESENT" else "SURFACE_UNPROVEN"
    except Exception:
        # Raw Playwright exceptions can include private URLs/paths. Suppress.
        return "CDP_READ_UNAVAILABLE"


# WH03 test-only signal. Intentionally not the eventual workflow registration
# interface; a user-visible signal by itself cannot authenticate ChatGPT.
# Read only exact synthetic test markers from displayed USER message bubbles,
# never return their value, another message, route URL or account data.
_WH03_TEST_SIGNAL_DOM = """
() => {
  const signal = /^WAKEHUB_TEST_REGISTER_V1:[0-9a-f]{64}$/;
  const isVisible = el => {
    if (!el || el.nodeType !== 1 || !el.getClientRects().length) return false;
    const style = getComputedStyle(el);
    return style && style.visibility === 'visible' && style.display !== 'none';
  };
  const nodes = [...document.querySelectorAll('*')];
  if (nodes.length > 20000) return 'TEST_SIGNAL_SCOPE_UNPROVEN';
  let total = 0, trusted = 0;
  const containers = new Set();
  for (const el of nodes) {
    if (!isVisible(el)) continue;
    const text = (el.textContent || '').trim();
    if (!signal.test(text)) continue;
    const children = [...(el.children || [])].filter(isVisible);
    if (children.some(child => signal.test((child.textContent || '').trim())))
      continue; // only count one leaf per visible occurrence
    total++;
    const excluded = el.closest('form,nav,header,button,textarea,input,[contenteditable="true"],[role="textbox"],[role="toolbar"],[role="navigation"]');
    if (excluded || el.closest('[data-message-author-role="assistant"]')) continue;
    const user = el.closest('[data-message-author-role="user"],[data-user-message-bubble]');
    if (!isVisible(user)) continue;
    const turn = user.closest('[data-message-id],[data-turn-id]') || user;
    const id = turn.getAttribute('data-message-id') || turn.getAttribute('data-turn-id');
    if (typeof id !== 'string' || !/^[0-9a-zA-Z._-]{1,64}$/.test(id)) continue;
    if (!containers.has(turn)) { containers.add(turn); trusted++; }
  }
  if (total === 0) return 'TEST_SIGNAL_NOT_FOUND';
  if (total !== 1 || trusted !== 1) return 'TEST_SIGNAL_AMBIGUOUS';
  return 'TEST_SIGNAL_PRESENT_UNATTESTED';
}
"""


def probe_wh03_test_registration_signal(*, endpoint: str = "http://127.0.0.1:9444",
                                        inventory=None) -> str:
    """Observe one explicit fresh-looking Chat A test signal; no enrollment.

    This DOES NOT prove freshness, private account, complete search domain,
    authenticated Chat A or native Send authority. Registration remains pending
    until a separately approved private owner binds the origin.
    """
    if not _is_windows() or type(endpoint) is not str or endpoint != "http://127.0.0.1:9444":
        return "NATIVE_DESKTOP_UNPROVEN"
    source = inventory if inventory is not None else _PlaywrightInventory()
    if not callable(getattr(source, "contexts", None)):
        return "CONTEXT_UNPROVEN"
    try:
        with source.contexts(endpoint) as contexts:
            if len(contexts) != 1:
                return "CONTEXT_UNPROVEN"
            pages = list(contexts[0].pages)
            if len(pages) > 32:
                return "CONTEXT_UNPROVEN"
            matches = [p for p in pages
                       if isinstance(getattr(p, "url", None), str)
                       and _CHAT.fullmatch(p.url.rstrip("/")) is not None]
            if len(matches) != 1:
                return ("MULTIPLE_CHAT_CANDIDATES" if matches
                        else "CHAT_A_CANDIDATE_NOT_FOUND")
            page = matches[0]
            guard = page.evaluate(_CHAT_A_READ_ONLY_SURFACE)
            if guard != "CANDIDATE_READY":
                return "DRAFT_PRESENT" if guard == "DRAFT_PRESENT" else "SURFACE_UNPROVEN"
            observed = page.evaluate(_WH03_TEST_SIGNAL_DOM)
            if observed in ("TEST_SIGNAL_NOT_FOUND", "TEST_SIGNAL_AMBIGUOUS",
                            "TEST_SIGNAL_PRESENT_UNATTESTED",
                            "TEST_SIGNAL_SCOPE_UNPROVEN"):
                return observed
            return "TEST_SIGNAL_SCOPE_UNPROVEN"
    except Exception:
        # Suppress Playwright exception text; private routes may appear in it.
        return "CDP_READ_UNAVAILABLE"




# Test-only WH03 precommit-aware reader. A validated *value object* is not
# private issuance or native authority. Its owner must load an immutable,
# one-use attempt from a separately enrolled private rendezvous store.
# Never select an arbitrary marker from the DOM or a public GitHub Issue.
@dataclass(frozen=True, repr=False)
class WH03SignalCommitment:
    registration_id: str
    marker_digest: str
    chat_url: str
    generation: int
    issued_ms: int
    expires_ms: int
    consumed: bool = False


_WH03_COMMITTED_SIGNAL_DOM = """
async (args) => {
  if (!args || typeof args.digest !== 'string' ||
      !/^[0-9a-f]{64}$/.test(args.digest))
    return 'TEST_COMMITMENT_INVALID';
  if (!globalThis.crypto || !crypto.subtle ||
      typeof TextEncoder !== 'function')
    return 'TEST_SIGNAL_SCOPE_UNPROVEN';
  const signal = /^WAKEHUB_TEST_REGISTER_V1:[0-9a-f]{64}$/;
  const visible = el => {
    if (!el || el.nodeType !== 1 || !el.getClientRects().length) return false;
    const style = getComputedStyle(el);
    return style && style.visibility === 'visible' && style.display !== 'none';
  };
  const nodes = [...document.querySelectorAll('*')];
  if (nodes.length > 20000) return 'TEST_SIGNAL_SCOPE_UNPROVEN';
  let occurrences = 0, userTurns = 0;
  const visited = new Set();
  for (const el of nodes) {
    if (!visible(el)) continue;
    const text = (el.textContent || '').trim();
    if (!signal.test(text)) continue;
    const children = [...(el.children || [])].filter(visible);
    if (children.some(child => signal.test((child.textContent || '').trim())))
      continue;
    const bytes = new TextEncoder().encode(text);
    const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
      .map(x => x.toString(16).padStart(2,'0')).join('');
    // Old registrations use different commitments. Ignore them; do NOT
    // count an old marker as this attempt or let it block an eligible one.
    if (digest !== args.digest) continue;
    occurrences++;
    if (el.closest('form,nav,header,button,textarea,input,[contenteditable="true"],[role="textbox"],[role="toolbar"],[role="navigation"]') ||
        el.closest('[data-message-author-role="assistant"]')) continue;
    const user = el.closest('[data-message-author-role="user"],[data-user-message-bubble]');
    if (!visible(user)) continue;
    const turn = user.closest('[data-message-id],[data-turn-id]') || user;
    const id = turn.getAttribute('data-message-id') || turn.getAttribute('data-turn-id');
    if (typeof id !== 'string' || !/^[0-9a-zA-Z._-]{1,64}$/.test(id))
      continue;
    if (!visited.has(turn)) { visited.add(turn); userTurns++; }
  }
  if (occurrences === 0) return 'TEST_SIGNAL_NOT_FOUND';
  if (occurrences !== 1 || userTurns !== 1) return 'TEST_SIGNAL_AMBIGUOUS';
  return 'TEST_SIGNAL_MATCH_UNATTESTED';
}
"""



@dataclass(frozen=True, repr=False)
class WH03PreparedRegistration:
    """Private value for an independently enrolled same-chat signal carrier."""
    registration_id: str
    marker: str
    expires_ms: int


class WH03TestRegistrationLedger:
    """Small test-only private ledger. No native authority is supplied here.

    Each attempt gets its own random marker. Historical attempt records remain
    distinct, so prior Chat A markers never count toward a newer registration.
    A source-only mock authority is NOT evidence of native ChatGPT identity.
    """
    _STATES = frozenset({"PENDING", "OBSERVING", "SIGNAL_OBSERVED",
                         "TEST_REGISTERED", "HOLD"})

    def __init__(self, state_path: Path, repository_root: Path, *,
                 authority: OriginAuthority | None = None):
        self.authority = authority
        self._state = PrivateJsonState(
            Path(state_path), Path(repository_root),
            label="WH03_TEST_REGISTRATION", max_bytes=131072)

    def _authorized(self, attestation, operation: str, context: dict) -> None:
        if (type(attestation) is not LocalCallerAttestation or
                not attestation.is_trusted(
                    authority=self.authority, operation=operation,
                    context=context)):
            raise OriginError("TEST_REGISTRATION_AUTHORITY_REQUIRED")

    @classmethod
    def _read(cls, transaction) -> dict:
        data = transaction.read({"version": 1, "registrations": {}})
        if (type(data) is not dict or set(data) != {"version", "registrations"} or
                type(data["version"]) is not int or data["version"] != 1 or
                type(data["registrations"]) is not dict or len(data["registrations"]) > 64):
            raise OriginError("TEST_REGISTRATION_STATE_INVALID")
        digests = set()
        for registration_id, item in data["registrations"].items():
            if (not isinstance(registration_id, str) or
                    re.fullmatch(r"[0-9a-f]{32}", registration_id) is None or
                    type(item) is not dict or set(item) != {
                        "registration_id", "marker_digest", "chat_url",
                        "generation", "issued_ms", "expires_ms", "phase"} or
                    item["registration_id"] != registration_id or
                    item["phase"] not in cls._STATES):
                raise OriginError("TEST_REGISTRATION_STATE_INVALID")
            c = WH03SignalCommitment(registration_id=item["registration_id"],
                                     marker_digest=item["marker_digest"],
                                     chat_url=item["chat_url"],
                                     generation=item["generation"],
                                     issued_ms=item["issued_ms"],
                                     expires_ms=item["expires_ms"])
            if (_wh03_commitment_gate_shape(c) != "READY" or
                    c.marker_digest in digests):
                raise OriginError("TEST_REGISTRATION_STATE_INVALID")
            digests.add(c.marker_digest)
        return data

    def prepare(self, *, chat_url: str, generation: int,
                attestation: LocalCallerAttestation | None,
                lease_seconds: int = 90) -> WH03PreparedRegistration:
        """Commit to private state BEFORE a trusted same-chat carrier emits."""
        if (type(chat_url) is not str or _CHAT.fullmatch(chat_url) is None or
                type(generation) is not int or generation < 1 or
                type(lease_seconds) is not int or not 1 <= lease_seconds <= 300):
            raise OriginError("TEST_REGISTRATION_INPUT_INVALID")
        self._authorized(attestation, "test_registration_prepare",
                         {"chat_url": chat_url, "generation": generation})
        try:
            with self._state.locked() as transaction:
                data = self._read(transaction)
                if len(data["registrations"]) >= 64:
                    raise OriginError("TEST_REGISTRATION_STORE_FULL")
                registration_id = secrets.token_hex(16)
                marker = "WAKEHUB_TEST_REGISTER_V1:" + secrets.token_hex(32)
                digest = hashlib.sha256(marker.encode("ascii")).hexdigest()
                if registration_id in data["registrations"] or any(
                    x["marker_digest"] == digest for x in data["registrations"].values()
                ):
                    raise OriginError("TEST_REGISTRATION_REPLAY")
                now = time.time_ns() // 1_000_000
                data["registrations"][registration_id] = {
                    "registration_id": registration_id,
                    "marker_digest": digest, "chat_url": chat_url,
                    "generation": generation, "issued_ms": now,
                    "expires_ms": now + lease_seconds * 1000,
                    "phase": "PENDING",
                }
                transaction.write(data)
                return WH03PreparedRegistration(registration_id, marker,
                                                now + lease_seconds * 1000)
        except PrivateStateError as exc:
            raise OriginError("TEST_REGISTRATION_STORE_UNAVAILABLE") from exc

    def observe(self, *, registration_id: str,
                attestation: LocalCallerAttestation | None,
                inventory=None) -> str:
        """One-shot CDP read; pre-write OBSERVING prevents ambiguous replays."""
        self._authorized(attestation, "test_registration_observe",
                         {"registration_id": registration_id})
        try:
            with self._state.locked() as transaction:
                data = self._read(transaction)
                record = data["registrations"].get(registration_id)
                if record is None:
                    return "TEST_REGISTRATION_NOT_FOUND"
                if record["phase"] != "PENDING":
                    return "TEST_REGISTRATION_REPLAY"
                value = WH03SignalCommitment(**{
                    k: record[k] for k in ("registration_id", "marker_digest",
                        "chat_url", "generation", "issued_ms", "expires_ms")})
                gate = _wh03_commitment_gate(value)
                if gate != "READY":
                    record["phase"] = "HOLD"
                    transaction.write(data)
                    return gate
                record["phase"] = "OBSERVING"
                transaction.write(data)  # durable boundary before browser read
                try:
                    result = probe_wh03_committed_registration_signal(
                        commitment=value, inventory=inventory)
                except Exception:
                    result = "CDP_READ_UNAVAILABLE"
                record["phase"] = ("SIGNAL_OBSERVED" if
                                   result == "TEST_SIGNAL_MATCH_UNATTESTED"
                                   else "HOLD")
                transaction.write(data)
                return result
        except PrivateStateError:
            return "TEST_REGISTRATION_STORE_UNAVAILABLE"

    def complete(self, *, registration_id: str,
                 origin: PrivateOriginSnapshot,
                 attestation: LocalCallerAttestation | None) -> str:
        """Requires independently enrolled native exact-chat proof verifier.

        The browser text matcher alone never returns TEST_REGISTERED. The
        authority must independently verify origin and caller custody; a public
        Issue/marker/boolean cannot implement this native decision.
        """
        self._authorized(attestation, "test_registration_complete",
                         {"registration_id": registration_id})
        try:
            with self._state.locked() as transaction:
                data = self._read(transaction)
                record = data["registrations"].get(registration_id)
                if record is None:
                    return "TEST_REGISTRATION_NOT_FOUND"
                if record["phase"] != "SIGNAL_OBSERVED":
                    return "TEST_REGISTRATION_NOT_VERIFIED"
                if (not _valid_origin(origin) or
                        origin.chat_url != record["chat_url"] or
                        origin.generation != record["generation"] or
                        origin.cdp_endpoint != "http://127.0.0.1:9444"):
                    return "TEST_CHAT_ROUTE_MISMATCH"
                if time.time_ns() // 1_000_000 >= record["expires_ms"]:
                    record["phase"] = "HOLD"
                    transaction.write(data)
                    return "TEST_COMMITMENT_EXPIRED"
                verifier = getattr(self.authority,
                                   "authenticate_wh03_test_registration", None)
                # Enrollment/implementation of this exact private native owner
                # is a separate effect: not present in the Skills/GitHub source.
                if not callable(verifier):
                    return "TEST_CHAT_ORIGIN_UNATTESTED"
                try:
                    proof = verifier(registration_id=registration_id,
                                     record=dict(record), origin=origin)
                except Exception:
                    return "TEST_CHAT_ORIGIN_UNATTESTED"
                if proof is not True:
                    return "TEST_CHAT_ORIGIN_UNATTESTED"
                record["phase"] = "TEST_REGISTERED"
                transaction.write(data)
                return "TEST_REGISTERED"
        except PrivateStateError:
            return "TEST_REGISTRATION_STORE_UNAVAILABLE"


def _wh03_commitment_gate_shape(value: WH03SignalCommitment) -> str:
    """Validate immutable commitment shape, excluding wall-clock expiry."""
    if (type(value) is not WH03SignalCommitment or
            type(value.registration_id) is not str or
            re.fullmatch(r"[a-z0-9][a-z0-9._-]{7,63}", value.registration_id) is None or
            type(value.marker_digest) is not str or
            re.fullmatch(r"[0-9a-f]{64}", value.marker_digest) is None or
            type(value.chat_url) is not str or
            _CHAT.fullmatch(value.chat_url) is None or
            type(value.generation) is not int or value.generation < 1 or
            type(value.issued_ms) is not int or
            type(value.expires_ms) is not int or
            not (1 <= value.expires_ms - value.issued_ms <= 300000) or
            type(value.consumed) is not bool):
        return "TEST_COMMITMENT_INVALID"
    if value.consumed:
        return "TEST_COMMITMENT_REPLAY"
    return "READY"


def _wh03_commitment_gate(value: WH03SignalCommitment | None) -> str:
    if value is None:
        return "TEST_COMMITMENT_REQUIRED"
    shape = _wh03_commitment_gate_shape(value)
    if shape != "READY":
        return shape
    now_ms = time.time_ns() // 1_000_000
    if now_ms < value.issued_ms:
        return "TEST_COMMITMENT_NOT_ACTIVE"
    if now_ms >= value.expires_ms:
        return "TEST_COMMITMENT_EXPIRED"
    return "READY"


def probe_wh03_committed_registration_signal(
    *, commitment: WH03SignalCommitment | None = None,
    endpoint: str = "http://127.0.0.1:9444", inventory=None
) -> str:
    """Observe only the current precommitted attempt and exact local Chat A.

    Caller-provided commitments do not prove issuer, account, full search
    domain, fresh user consent, private custody or a registered origin.
    This read never sends, enrolls, or exports marker/chat/session data.
    The fixed GitHub operator must HOLD until an authenticated private owner
    supplies and consumes an independently issued, one-shot commitment.
    """
    gate = _wh03_commitment_gate(commitment)
    if gate != "READY":
        return gate
    if not _is_windows() or type(endpoint) is not str or endpoint != "http://127.0.0.1:9444":
        return "NATIVE_DESKTOP_UNPROVEN"
    source = inventory if inventory is not None else _PlaywrightInventory()
    if not callable(getattr(source, "contexts", None)):
        return "CONTEXT_UNPROVEN"
    try:
        with source.contexts(endpoint) as contexts:
            if len(contexts) != 1:
                return "CONTEXT_UNPROVEN"
            pages = list(contexts[0].pages)
            if len(pages) > 32:
                return "CONTEXT_UNPROVEN"
            candidates = [p for p in pages
                          if type(getattr(p, "url", None)) is str
                          and _CHAT.fullmatch(p.url.rstrip("/")) is not None]
            if len(candidates) != 1:
                return ("MULTIPLE_CHAT_CANDIDATES" if candidates
                        else "CHAT_A_CANDIDATE_NOT_FOUND")
            page = candidates[0]
            if page.url.rstrip("/") != commitment.chat_url:
                return "TEST_CHAT_ROUTE_MISMATCH"
            guard = page.evaluate(_CHAT_A_READ_ONLY_SURFACE)
            if guard != "CANDIDATE_READY":
                return "DRAFT_PRESENT" if guard == "DRAFT_PRESENT" else "SURFACE_UNPROVEN"
            observed = page.evaluate(
                _WH03_COMMITTED_SIGNAL_DOM, {"digest": commitment.marker_digest})
            if observed in ("TEST_SIGNAL_NOT_FOUND", "TEST_SIGNAL_AMBIGUOUS",
                            "TEST_SIGNAL_SCOPE_UNPROVEN",
                            "TEST_SIGNAL_MATCH_UNATTESTED"):
                return observed
            return "TEST_SIGNAL_SCOPE_UNPROVEN"
    except Exception:
        # Browser exception messages might include private paths/chat URLs.
        return "CDP_READ_UNAVAILABLE"



class LocalChromeDoorbellAdapter:
    """UI-only exact-chat doorbell, explicitly opt-in, without semantic authority."""

    def __init__(self, *, repository: str = "trung-via/ai-commerce",
                 allow_submit: bool = False,
                 inventory: BrowserInventory | None = None,
                 preload_exact_chat: bool = False,
                 send_only: bool = False,
                 fixed_check_test: bool = False):
        if (type(fixed_check_test) is not bool or
                (fixed_check_test and
                 (send_only is not True or repository != "trung-via/ai-commerce"))):
            raise ValueError("DOORBELL_TEST_SCOPE_INVALID")
        if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
            raise ValueError("DOORBELL_REPOSITORY_INVALID")
        self.repository = repository
        self.allow_submit = allow_submit is True
        self.inventory = inventory or _PlaywrightInventory()
        self.preload_exact_chat = preload_exact_chat is True
        self.send_only = send_only is True
        # Only an explicitly configured trusted operator may select this mode.
        self.fixed_check_test = fixed_check_test is True

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
        # D2's verified origin/consent and durable UNKNOWN precede this adapter.
        # The new pre-Wake navigation never acts as a Send authorization.
        if self.preload_exact_chat:
            code = (self.inventory.preload(binding) if
                    callable(getattr(self.inventory, "preload", None))
                    else "PRELOAD_UNAVAILABLE")
            if code not in ("EXACT_CHAT_PRESENT", "EXACT_CHAT_OPENED"):
                return receipt("UNSUPPORTED", code)
        doorbell = _doorbell_text(event_id, self.repository,
                                  send_only=self.send_only,
                                  fixed_check_test=self.fixed_check_test)
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


class LocalChromeObserverAdapter:
    """Bounded, truly read-only Chrome CDP 127.0.0.1:9444 observer adapter.

    Implements TrustedPrivateBrowserObserver for source-merged WakeHub F1A.
    Disconnects Playwright client only. Never launches, opens tabs, navigates,
    types, clicks, sends, or alters user session state.
    """
    is_local_chrome_observer = True

    def __init__(self, *,
                 inventory: BrowserInventory | None = None,
                 enrolled_origin: PrivateOriginSnapshot | None = None,
                 scope_witness: Callable[..., bool] | CompleteSearchDomainWitness | None = None,
                 enrolled_chats: tuple[str, ...] | list[str] | set[str] | None = None,
                 native_authority: bool = False,
                 native_evidence_consumer: VerifiedNativeOriginEvidenceConsumer | None = None,
                 profile_id: str | None = None,
                 account_id: str | None = None,
                 session_id: str | None = None,
                 login_id: str | None = None):
        self.inventory = inventory or _PlaywrightInventory()
        if enrolled_origin is not None:
            if not _valid_origin(enrolled_origin):
                raise OriginError("UNVERIFIED_ORIGIN")
        self.enrolled_origin = enrolled_origin
        self.scope_witness = scope_witness
        self.enrolled_chats = tuple(enrolled_chats) if enrolled_chats is not None else None
        self.native_authority = native_authority is True
        self.native_evidence_consumer = native_evidence_consumer
        self.profile_id = profile_id
        self.account_id = account_id
        self.session_id = session_id
        self.login_id = login_id
        self._used_nonces: set[str] = set()
        self._captured_requests: dict[str, PrivateOriginSnapshot] = {}

    def __repr__(self) -> str:
        return "LocalChromeObserverAdapter(<private>)"

    @contextmanager
    def _browser_pages(self, endpoint: str):
        if hasattr(self.inventory, "contexts"):
            with self.inventory.contexts(endpoint) as contexts:
                if len(contexts) != 1:
                    raise OriginError("SEARCH_COVERAGE_UNPROVEN")
                ctx_pages = list(contexts[0].pages)
                if not ctx_pages:
                    raise OriginError("SEARCH_COVERAGE_UNPROVEN")
                yield ctx_pages
        elif hasattr(self.inventory, "pages"):
            with self.inventory.pages(endpoint) as pages:
                p_list = list(pages)
                if not p_list:
                    raise OriginError("SEARCH_COVERAGE_UNPROVEN")
                yield p_list
        else:
            raise OriginError("SEARCH_COVERAGE_UNPROVEN")

    def capture_origin(self, *, request_id: str,
                       binding: ResolvedBinding) -> PrivateOriginSnapshot:
        """Capture and prove the explicitly supplied opened route and session."""
        if not _is_windows():
            raise OriginError("SEARCH_COVERAGE_UNPROVEN")
        if not _private_id(request_id):
            raise OriginError("RENDEZVOUS_INPUT_INVALID")
        if (type(binding) is not ResolvedBinding or
                not _private_id(binding.route_id) or
                type(binding.generation) is not int or binding.generation < 1 or
                type(binding.chat_url) is not str or _CHAT.fullmatch(binding.chat_url) is None or
                not _endpoint(binding.cdp_endpoint) or
                urlsplit(binding.cdp_endpoint).port != 9444):
            raise OriginError("RENDEZVOUS_ORIGIN_INVALID")

        if self.enrolled_origin is None:
            raise OriginError("UNVERIFIED_ORIGIN")
        if (self.enrolled_origin.route_id != binding.route_id or
                self.enrolled_origin.generation != binding.generation or
                self.enrolled_origin.chat_url != binding.chat_url or
                self.enrolled_origin.cdp_endpoint != binding.cdp_endpoint):
            raise OriginError("RENDEZVOUS_ORIGIN_INVALID")

        pid = self.enrolled_origin.profile_id
        aid = self.enrolled_origin.account_id
        sid = self.enrolled_origin.session_id
        lid = self.enrolled_origin.login_id

        try:
            with self._browser_pages(binding.cdp_endpoint) as pages:
                matching = [p for p in pages
                            if isinstance(getattr(p, "url", None), str) and
                            p.url.rstrip("/") == binding.chat_url]
                if len(matching) != 1:
                    raise OriginError("SEARCH_COVERAGE_UNPROVEN" if len(matching) > 1
                                     else "RENDEZVOUS_ORIGIN_INVALID")
                page = matching[0]
                if hasattr(page, "set_default_timeout"):
                    page.set_default_timeout(3000)
                args = {**_ARGS, "url": binding.chat_url,
                        "profile_id": pid, "account_id": aid}
                result = page.evaluate(_OBSERVER_CHECK, args)
                if result == "PROFILE_UNPROVEN":
                    raise OriginError("PROFILE_UNPROVEN")
                if result == "PROFILE_MISMATCH":
                    raise OriginError("PROFILE_MISMATCH")
                if result == "ACCOUNT_UNPROVEN":
                    raise OriginError("ACCOUNT_UNPROVEN")
                if result == "ACCOUNT_MISMATCH":
                    raise OriginError("ACCOUNT_MISMATCH")
                if result != "READY":
                    raise OriginError("RENDEZVOUS_ORIGIN_INVALID")
                if getattr(page, "url", "").rstrip("/") != binding.chat_url:
                    raise OriginError("RENDEZVOUS_ORIGIN_INVALID")
        except OriginError:
            raise
        except Exception:
            raise OriginError("SEARCH_COVERAGE_UNPROVEN") from None

        snapshot = PrivateOriginSnapshot(
            route_id=binding.route_id,
            generation=binding.generation,
            chat_url=binding.chat_url,
            cdp_endpoint=binding.cdp_endpoint,
            profile_id=pid,
            account_id=aid,
            session_id=sid,
            login_id=lid,
        )
        if not _valid_origin(snapshot):
            raise OriginError("RENDEZVOUS_ORIGIN_INVALID")
        self._captured_requests[request_id] = snapshot
        return snapshot

    def search_and_reprove(self, emission: RendezvousEmission, *,
                           nonce: str) -> RendezvousObservation:
        """Enumerate all complete marker occurrences across search domain and reprove origin."""
        if not _is_windows():
            raise OriginError("SEARCH_COVERAGE_UNPROVEN")
        if (type(emission) is not RendezvousEmission or
                not _private_id(emission.request_id) or
                type(emission.marker) is not str or not _MARKER.fullmatch(emission.marker) or
                type(emission.marker_digest) is not str or
                hashlib.sha256(emission.marker.encode("ascii")).hexdigest() != emission.marker_digest or
                not _valid_origin(emission.origin)):
            raise OriginError("RENDEZVOUS_OBSERVATION_INVALID")
        if urlsplit(emission.origin.cdp_endpoint).port != 9444:
            raise OriginError("SEARCH_COVERAGE_UNPROVEN")
        if type(nonce) is not str or len(nonce) < 16:
            raise OriginError("RENDEZVOUS_OBSERVATION_INVALID")

        if self.enrolled_origin is None:
            raise OriginError("UNVERIFIED_ORIGIN")
        if self.enrolled_origin != emission.origin:
            raise OriginError("RENDEZVOUS_ORIGIN_MISMATCH")

        if self._captured_requests.get(emission.request_id) != emission.origin:
            raise OriginError("RENDEZVOUS_ORIGIN_MISMATCH")

        if self.native_authority:
            raise OriginError("SEARCH_COVERAGE_UNPROVEN")

        if nonce in self._used_nonces:
            raise OriginError("RENDEZVOUS_REPLAY")
        self._used_nonces.add(nonce)

        hits: list[RendezvousHit] = []
        try:
            with self._browser_pages(emission.origin.cdp_endpoint) as pages:
                if not pages:
                    raise OriginError("SEARCH_COVERAGE_UNPROVEN")

                observed_urls = tuple(
                    p.url.rstrip("/") for p in pages
                    if isinstance(getattr(p, "url", None), str)
                )

                if self.scope_witness is not None:
                    if hasattr(self.scope_witness, "is_search_scope_complete"):
                        try:
                            scope_ok = self.scope_witness.is_search_scope_complete(
                                origin=emission.origin, observed_urls=observed_urls, nonce=nonce
                            )
                        except Exception:
                            scope_ok = False
                    elif callable(self.scope_witness):
                        try:
                            scope_ok = self.scope_witness(observed_urls, emission.origin)
                        except TypeError:
                            scope_ok = self.scope_witness(observed_urls)
                    else:
                        scope_ok = False
                    if not scope_ok:
                        raise OriginError("SEARCH_COVERAGE_UNPROVEN")
                elif self.enrolled_chats is not None:
                    if not all(chat.rstrip("/") in observed_urls for chat in self.enrolled_chats):
                        raise OriginError("SEARCH_COVERAGE_UNPROVEN")
                else:
                    raise OriginError("SEARCH_COVERAGE_UNPROVEN")

                search_args = {"marker": emission.marker}
                for page in pages:
                    if hasattr(page, "set_default_timeout"):
                        page.set_default_timeout(3000)
                    try:
                        raw = page.evaluate(_SEARCH_MARKER, search_args)
                    except Exception:
                        raise OriginError("SEARCH_COVERAGE_UNPROVEN") from None
                    if type(raw) is not dict or "matches" not in raw or type(raw["matches"]) is not list:
                        raise OriginError("SEARCH_COVERAGE_UNPROVEN")
                    # Count copied marker as ambiguity/HOLD without fabricating valid user message
                    if raw.get("ambiguous") or raw.get("total", 0) > len(raw["matches"]):
                        raise OriginError("RENDEZVOUS_SEARCH_NOT_UNIQUE")
                    for m in raw["matches"]:
                        if type(m) is not dict or "message_id" not in m or not _private_id(m["message_id"]):
                            raise OriginError("RENDEZVOUS_OBSERVATION_INVALID")
                        p_url = getattr(page, "url", "")
                        if p_url.rstrip("/") == emission.origin.chat_url:
                            hit_origin = emission.origin
                        else:
                            hit_origin = replace(emission.origin, chat_url=p_url.rstrip("/"))
                        hits.append(RendezvousHit(
                            marker=emission.marker,
                            origin=hit_origin,
                            message_id=m["message_id"],
                        ))

                if len(hits) == 1:
                    matching = [p for p in pages
                                if isinstance(getattr(p, "url", None), str) and
                                p.url.rstrip("/") == emission.origin.chat_url]
                    if len(matching) != 1:
                        raise OriginError("RENDEZVOUS_ORIGIN_INVALID")
                    chat_page = matching[0]
                    if hasattr(chat_page, "set_default_timeout"):
                        chat_page.set_default_timeout(3000)
                    check_args = {**_ARGS, "url": emission.origin.chat_url,
                                  "profile_id": emission.origin.profile_id,
                                  "account_id": emission.origin.account_id}
                    res = chat_page.evaluate(_OBSERVER_CHECK, check_args)
                    if res == "PROFILE_UNPROVEN":
                        raise OriginError("PROFILE_UNPROVEN")
                    if res == "PROFILE_MISMATCH":
                        raise OriginError("PROFILE_MISMATCH")
                    if res == "ACCOUNT_UNPROVEN":
                        raise OriginError("ACCOUNT_UNPROVEN")
                    if res == "ACCOUNT_MISMATCH":
                        raise OriginError("ACCOUNT_MISMATCH")
                    if res != "READY":
                        raise OriginError("RENDEZVOUS_ORIGIN_INVALID")
                    if getattr(chat_page, "url", "").rstrip("/") != emission.origin.chat_url:
                        raise OriginError("RENDEZVOUS_ORIGIN_INVALID")
        except OriginError:
            raise
        except Exception:
            raise OriginError("SEARCH_COVERAGE_UNPROVEN") from None

        return RendezvousObservation(
            request_id=emission.request_id,
            marker_digest=emission.marker_digest,
            nonce=nonce,
            hits=tuple(hits),
            opened_origin=emission.origin,
        )


LocalChromeBrowserObserver = LocalChromeObserverAdapter
LocalChromePrivateObserver = LocalChromeObserverAdapter
ChromeCdpObserverAdapter = LocalChromeObserverAdapter

