"""Read-only exact-chat @-plugin selection diagnostics for a future D5 Wake.

Intentionally NON-DELIVERING: observes only the existing registered ChatGPT tab
and returns allowlisted status codes for two known plugin names. Neither this
probe nor a DOM candidate proves host-authenticated plugin invocation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .conversation_binding import ResolvedBinding, _CHAT, _endpoint

BRAIN = "AI Commerce Brain"
ENGINEERING = "AI Commerce Engineering Workflow"
VALID_STATES = frozenset({
    "ABSENT", "PLAIN_TEXT_ONLY", "STRUCTURED_CANDIDATE", "UNVERIFIED_STRUCTURE", "AMBIGUOUS",
})
VALID_CODES = frozenset({
    "PROBE_DISABLED", "BINDING_INVALID", "EXACT_CHAT_TAB_NOT_FOUND",
    "AMBIGUOUS_CHAT_TARGETS", "TARGET_PAGE_CHANGED", "SURFACE_UNPROVEN",
    "GENERATION_ACTIVE", "PROBE_COMPLETE", "CDP_UNAVAILABLE",
})

# Private transcript, URL and DOM content never leave the browser. A detected
# noneditable/mention-like element is only a structural candidate until a
# specific host chip representation is independently confirmed.
_PLUGIN_PROBE_JS = r"""(args) => {
  const visible = e => e && typeof e.getClientRects === 'function' &&
    e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility === 'visible';
  if (location.href.replace(/\/$/, '') !== args.url)
    return {code:'TARGET_PAGE_CHANGED'};
  const mains = [...document.querySelectorAll('main')].filter(visible);
  if (mains.length !== 1 ||
      [...document.querySelectorAll(args.login)].some(visible) ||
      [...document.querySelectorAll(args.work)].some(visible))
    return {code:'SURFACE_UNPROVEN'};
  const editors = [...document.querySelectorAll(args.composer)].filter(visible);
  if (editors.length !== 1) return {code:'SURFACE_UNPROVEN'};
  if ([...document.querySelectorAll(args.stop)].some(visible))
    return {code:'GENERATION_ACTIVE'};
  const editor = editors[0], forms = [];
  for (let p = editor.parentElement; p; p = p.parentElement)
    if (p.tagName === 'FORM') forms.push(p);
  if (forms.length !== 1) return {code:'SURFACE_UNPROVEN'};
  const nodes = [...forms[0].querySelectorAll('*')];
  if (nodes.length > 1200) return {code:'SURFACE_UNPROVEN'};
  const normalized = e => String(e.textContent || '').trim().replace(/\s+/g, ' ');
  const marker = e => {
    // A generic contenteditable=false or mention container is NOT enough
    // to identify a plugin chip: plain text can inherit those wrappers.
    // Never return DOM, attribute values, user content or plugin IDs.
    let weak = false;
    for (let p=e, depth=0; p && p !== forms[0] && depth<4;
         p=p.parentElement, depth++) {
      const typ = String(p.getAttribute('data-type') || '');
      const tid = String(p.getAttribute('data-testid') || '');
      if (p.getAttribute('data-plugin-id') !== null ||
          /^(plugin|plugin-mention)$/i.test(typ) ||
          /(plugin-mention|plugin-chip)/i.test(tid))
        return 'PLUGIN_MARKER';
      if (p.getAttribute('data-mention-id') !== null ||
          /^mention$/i.test(typ) || /(mention-chip)/i.test(tid) ||
          p.getAttribute('contenteditable') === 'false')
        weak = true;
    }
    return weak ? 'GENERIC_STRUCTURE' : 'NONE';
  };
  const state = name => {
    const found = nodes.filter(e => visible(e) && normalized(e) === name &&
      ![...e.children].some(c => normalized(c) === name));
    if (!found.length) return 'ABSENT';
    if (found.length !== 1) return 'AMBIGUOUS';
    const kind = marker(found[0]);
    return kind === 'PLUGIN_MARKER' ? 'STRUCTURED_CANDIDATE' :
      kind === 'GENERIC_STRUCTURE' ? 'UNVERIFIED_STRUCTURE' : 'PLAIN_TEXT_ONLY';
  };
  return {code:'PROBE_COMPLETE',
          brain:state(args.brain), engineering:state(args.engineering)};
}"""

# Kept in sync with current D4 selectors. Do not import D4's send/click scripts.
_COMPOSER = ('#prompt-textarea[contenteditable="true"], '
             'main form [contenteditable="true"][role="textbox"][aria-multiline="true"]')
_STOP = '[data-testid="stop-button"]'
_LOGIN = '[data-testid="login-button"], a[href^="/auth/login"]'
_WORK = ('[data-workspace-type="team"], [data-workspace-type="enterprise"], '
         '[data-workspace-type="business"], [data-testid="work-composer"]')


@dataclass(frozen=True)
class PluginProbeResult:
    code: str
    brain: str = "ABSENT"
    engineering: str = "ABSENT"

    @property
    def host_selection_proven(self) -> bool:
        # Do not use DOM heuristics as a Send authorization.
        return False


class BrowserInventory(Protocol):
    def pages(self, endpoint: str): ...


class ReadOnlyPluginSelectionProbe:
    """Attach-only, fail-closed; no typing, menu clicking, navigation or Send."""

    def __init__(self, *, enabled: bool = False,
                 inventory: BrowserInventory | None = None):
        self.enabled = enabled is True
        self.inventory = inventory

    def inspect(self, binding: ResolvedBinding) -> PluginProbeResult:
        if (type(binding) is not ResolvedBinding or
                not isinstance(binding.chat_url, str) or
                not _CHAT.fullmatch(binding.chat_url) or
                not _endpoint(binding.cdp_endpoint)):
            return PluginProbeResult("BINDING_INVALID")
        if not self.enabled:
            return PluginProbeResult("PROBE_DISABLED")
        try:
            # Import after opt-in and validation, with no implicit browser launch.
            from .cdp_doorbell import _PlaywrightInventory
            inventory = self.inventory or _PlaywrightInventory()
            with inventory.pages(binding.cdp_endpoint) as pages:
                matching = [page for page in pages
                            if isinstance(page.url, str) and
                            page.url.rstrip("/") == binding.chat_url]
                if len(matching) != 1:
                    return PluginProbeResult(
                        "EXACT_CHAT_TAB_NOT_FOUND" if not matching
                        else "AMBIGUOUS_CHAT_TARGETS")
                page = matching[0]
                raw = page.evaluate(_PLUGIN_PROBE_JS, {
                    "url": binding.chat_url, "composer": _COMPOSER,
                    "login": _LOGIN, "work": _WORK, "stop": _STOP,
                    "brain": BRAIN, "engineering": ENGINEERING,
                })
                if not isinstance(raw, dict):
                    return PluginProbeResult("SURFACE_UNPROVEN")
                code = raw.get("code")
                if code not in VALID_CODES:
                    return PluginProbeResult("SURFACE_UNPROVEN")
                if code != "PROBE_COMPLETE":
                    return PluginProbeResult(code)
                if (raw.get("brain") not in VALID_STATES or
                        raw.get("engineering") not in VALID_STATES or
                        set(raw) != {"code", "brain", "engineering"}):
                    return PluginProbeResult("SURFACE_UNPROVEN")
                return PluginProbeResult(code, raw["brain"], raw["engineering"])
        except Exception:
            # No browser error contents (which might contain account/URL data).
            return PluginProbeResult("CDP_UNAVAILABLE")
