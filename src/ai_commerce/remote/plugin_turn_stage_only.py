"""D5 explicit per-turn plugin-chip staging in an EXISTING exact chat.

Opt-in, Windows-only, NO message Send, NO Wake, NO navigation, NO retry.
The caller must clear any staged draft/chips manually; UI chips do not prove
host plugin invocation. This module is not installed in an active wake path.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import os
from typing import Protocol

from .conversation_binding import ResolvedBinding, _CHAT, _endpoint
from .plugin_selection_probe import (
    BRAIN, ENGINEERING, _COMPOSER, _LOGIN, _WORK, _STOP,
    _PLUGIN_PROBE_JS,
)
from .plugin_turn_handoff import TurnHandoffError, preview_next_turn

# Read-only JavaScript: never returns private editor text or chat URL.
_PRECHECK_JS = r"""(args) => {
  const visible = e => e && e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility === 'visible';
  if (location.href.replace(/\/$/, '') !== args.url) return 'TARGET_PAGE_CHANGED';
  if ([...document.querySelectorAll('main')].filter(visible).length !== 1 ||
      [...document.querySelectorAll(args.login)].some(visible) ||
      [...document.querySelectorAll(args.work)].some(visible))
    return 'SURFACE_UNPROVEN';
  if ([...document.querySelectorAll(args.stop)].some(visible))
    return 'GENERATION_ACTIVE';
  const editors = [...document.querySelectorAll(args.composer)].filter(visible);
  if (editors.length !== 1) return 'SURFACE_UNPROVEN';
  const editor=editors[0], forms=[];
  for (let p=editor.parentElement;p;p=p.parentElement)
    if (p.tagName==='FORM') forms.push(p);
  if (forms.length !== 1 || editor.getAttribute('aria-disabled')==='true')
    return 'SURFACE_UNPROVEN';
  if (args.phase === 'EMPTY') {
    if ((editor.textContent || '') !== '' || editor.querySelector('[data-type="mention"]'))
      return 'DRAFT_PRESENT';
    return 'READY';
  }
  if (args.phase === 'FULL_NAME') {
    // Read only a boolean; do not return text, URL, or DOM.
    const plain=(editor.textContent || '').trim();
    return plain === args.full || plain === args.name
      ? 'FULL_NAME_PRESENT' : 'FULL_NAME_UNPROVEN';
  }
  return 'SURFACE_UNPROVEN';
}"""

_ARGS = {"composer": _COMPOSER, "stop": _STOP, "login": _LOGIN, "work": _WORK,
         "brain": BRAIN, "engineering": ENGINEERING}
_ALLOWED = frozenset({"READY", "DRAFT_PRESENT", "SURFACE_UNPROVEN",
                      "GENERATION_ACTIVE", "TARGET_PAGE_CHANGED",
                      "FULL_NAME_PRESENT", "FULL_NAME_UNPROVEN"})


def _is_windows() -> bool:
    return os.name == "nt"


class ChipInventory(Protocol):
    def pages(self, endpoint: str) -> AbstractContextManager: ...


@dataclass(frozen=True)
class StageOnlyResult:
    code: str
    phase: str = "NOT_STARTED"
    brain: str = "ABSENT"
    engineering: str = "ABSENT"

    @property
    def sent(self) -> bool:
        return False

    @property
    def host_selection_proven(self) -> bool:
        return False


class ExplicitTurnChipStager:
    """One operator-authorized chip input, leaving the composer UNSENT.

    No implicit fallback, no browser launch, no Send and no draft cleanup.
    Every ambiguous partial mutation returns CLEAR_MANUALLY, not an exception
    containing browser details. A session in SUBMISSION_UNKNOWN is ineligible.
    """
    def __init__(self, *, enabled: bool = False, inventory: ChipInventory | None = None):
        self.enabled = enabled is True
        self.inventory = inventory

    def stage(self, binding: ResolvedBinding, session: dict,
              *, operator_approved: bool = False) -> StageOnlyResult:
        if not self.enabled:
            return StageOnlyResult("STAGER_DISABLED")
        if operator_approved is not True:
            return StageOnlyResult("OPERATOR_APPROVAL_REQUIRED")
        if not _is_windows():
            return StageOnlyResult("WINDOWS_INTERACTIVE_REQUIRED")
        if (type(binding) is not ResolvedBinding or
                not isinstance(binding.chat_url, str) or
                not _CHAT.fullmatch(binding.chat_url) or
                not _endpoint(binding.cdp_endpoint)):
            return StageOnlyResult("BINDING_INVALID")
        try:
            preview = preview_next_turn(session)
        except (TurnHandoffError, TypeError):
            return StageOnlyResult("TURN_NOT_READY")
        name = preview["plugin_alias_for_operator_selection"]
        if name not in (BRAIN, ENGINEERING):
            return StageOnlyResult("ROLE_UNSUPPORTED")
        other = "engineering" if name == BRAIN else "brain"
        own = "brain" if name == BRAIN else "engineering"
        args = {**_ARGS, "url": binding.chat_url, "name": name,
                "full": "@" + name}
        phase = "ATTACH"
        try:
            from .cdp_doorbell import _PlaywrightInventory
            inventory = self.inventory or _PlaywrightInventory()
            with inventory.pages(binding.cdp_endpoint) as pages:
                matches = [p for p in pages if isinstance(p.url, str) and
                           p.url.rstrip("/") == binding.chat_url]
                if len(matches) != 1:
                    return StageOnlyResult("EXACT_CHAT_NOT_UNIQUE")
                page = matches[0]
                def check(stage):
                    value = page.evaluate(_PRECHECK_JS, {**args, "phase": stage})
                    return value if type(value) is str and value in _ALLOWED else "SURFACE_UNPROVEN"
                ready = check("EMPTY")
                if ready != "READY":
                    return StageOnlyResult(ready)
                editor = page.locator(_COMPOSER)
                if editor.count() != 1:
                    return StageOnlyResult("SURFACE_UNPROVEN")
                editor.focus(timeout=5000)
                if check("EMPTY") != "READY":
                    return StageOnlyResult("COMPOSER_CHANGED")
                phase = "TYPE_NAME"
                editor.press_sequentially("@" + name, delay=65, timeout=20000)
                phase = "VERIFY_NAME"
                if check("FULL_NAME") != "FULL_NAME_PRESENT":
                    return StageOnlyResult("INCOMPLETE_NAME_CLEAR_MANUALLY", phase)
                phase = "TYPE_SPACE"
                editor.press("Space", timeout=5000)
                phase = "OBSERVE_CHIP"
                page.wait_for_timeout(700)
                if page.url.rstrip("/") != binding.chat_url:
                    return StageOnlyResult("TARGET_CHANGED_CLEAR_MANUALLY", phase)
                raw = page.evaluate(_PLUGIN_PROBE_JS, args)
                # Do not trust browser-returned extra fields or status strings.
                if (not isinstance(raw, dict) or set(raw) !=
                    {"code", "brain", "engineering"} or
                        raw["code"] != "PROBE_COMPLETE" or
                        raw[own] not in ("ABSENT", "PLAIN_TEXT_ONLY",
                                         "STRUCTURED_CANDIDATE", "AMBIGUOUS") or
                        raw[other] not in ("ABSENT", "PLAIN_TEXT_ONLY",
                                           "STRUCTURED_CANDIDATE", "AMBIGUOUS")):
                    return StageOnlyResult("OBSERVATION_UNKNOWN_CLEAR_MANUALLY", phase)
                if raw[own] != "STRUCTURED_CANDIDATE" or raw[other] != "ABSENT":
                    return StageOnlyResult(
                        "CHIP_UNVERIFIED_CLEAR_MANUALLY", phase,
                        raw["brain"], raw["engineering"])
                return StageOnlyResult(
                    "INPUT_STAGED_NO_SEND", "COMPLETE", raw["brain"], raw["engineering"])
        except Exception:
            # A typing error can leave a partial composer. Never retry.
            return StageOnlyResult(
                "STAGING_UNKNOWN_CLEAR_MANUALLY" if phase != "ATTACH"
                else "CDP_UNAVAILABLE", phase)
