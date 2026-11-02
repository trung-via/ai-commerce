"""One guarded D5 readiness flow: Chrome CDP -> enrolled chat -> READY-for-prepare.

This is an explicitly invoked *local Windows* operator command, never an
unattended GitHub runner/ChatGPT Send/Wake worker. No private paths or URLs
are emitted. Each invocation makes at most one Chrome launch attempt and one
exact-chat preload attempt. Ambiguous effects always stop (no retry).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import chrome_acquisition
from .wake_readiness_cache import ChromeAttestationCache
from .local_completion_handoff import _private_identity
from .plugin_selection_probe_cli import load_registered_probe_route
from .plugin_turn_cli import _fresh_main_sha
from .plugin_turn_store import TurnSessionStore
from .wake_control_center import WakePanel

_FIELDS = frozenset({
    "version", "checkout", "chrome_config", "state_file", "registry", "route_id"
})
_GOOD_CHATS = frozenset({"EXACT_CHAT_OPENED", "EXACT_CHAT_PRESENT"})


def _blocked(code: str, *, chrome: str = "UNVERIFIED") -> dict:
    # Callers get ONE actionable outcome, not a checklist to rerun.
    # Uncertain side effects are never treated as recoverable.
    action = ("RECONCILE_NO_RETRY" if "UNKNOWN" in code or
              "AMBIGUOUS" in code or "ALREADY_ACTIVE" in code else
              "REVIEW_PINNED_SOURCE" if code == "SOURCE_SHA_CHANGED" else
              "REVIEW_ENROLLMENT" if code.startswith(("LEDGER_", "ROUTE_")) else
              "DIAGNOSE_NO_RELAUNCH")
    return {
        "status": "BLOCKED", "code": code, "next_action": action,
        "chrome": chrome, "wake_sent": False,
        "host_plugin_invocation_proven": False,
    }


def _ready(*, launched: bool, verification: str, chat_preloaded: bool) -> dict:
    return {
        "status": "READY_FOR_PREPARE",
        "chrome": "VERIFIED_9333", "chat": "EXACT_CHAT_PRESENT",
        "ledger": "READY", "launched": launched, "verification": verification,
        "auto_repaired": (["CHROME_LAUNCHED"] if launched else []) +
                         (["EXACT_CHAT_PRELOADED"] if chat_preloaded else []),
        "wake_sent": False,
        "host_plugin_invocation_proven": False,
    }


class ReadinessFlow:
    """Pure, testable sequencing across existing guarded components."""

    def __init__(self, *, acquire, panel: WakePanel, source_reader):
        self.acquire = acquire
        self.panel = panel
        self.source_reader = source_reader

    def execute(self, *, opt_in_launch: bool, opt_in_open_chat: bool) -> dict:
        # Gate on a fresh enrolled episode BEFORE touching the browser.
        before = self.panel._state()
        if before is None:
            return _blocked("LEDGER_UNAVAILABLE")
        if before["phase"] == "SUBMISSION_UNKNOWN":
            return _blocked("SUBMISSION_UNKNOWN_NO_RETRY")
        if before["phase"] != "READY":
            return _blocked("LEDGER_NOT_READY")
        binding = self.panel._route()
        if binding is None:
            return _blocked("ROUTE_NOT_ENROLLED_9333")
        try:
            if self.source_reader() != before["source_sha"]:
                return _blocked("SOURCE_SHA_CHANGED")
        except Exception:
            return _blocked("SOURCE_UNAVAILABLE")

        # Existing acquisition enforces exact dedicated profile, SID/DACL,
        # non-elevated interactive desktop, signed Chrome and loopback PID.
        # Never auto-clean a profile or retry an uncertain launch.
        try:
            acquired = self.acquire(opt_in_launch)
        except chrome_acquisition.AcquisitionBlocked as exc:
            return _blocked("CHROME_" + str(exc))
        except Exception:
            return _blocked("CHROME_OPERATION_UNKNOWN")
        if acquired.get("status") == "NOT_RUNNING":
            return _blocked("CHROME_NOT_RUNNING")
        if (acquired.get("status") != "READY_ATTACH" or
                acquired.get("cdp_verified") is not True):
            return _blocked("CHROME_NOT_VERIFIED")
        launched = acquired.get("launched") is True
        chat_preloaded = False
        try:
            state = self.panel.status()
            if (state["phase"] != "READY" or
                    state["route"] != "ENROLLED" or
                    state["tcp"] != "REACHABLE_UNVERIFIED"):
                return _blocked("ROUTE_LEDGER_OR_TCP_CHANGED",
                                chrome="VERIFIED_9333")
            chat = self.panel.inspect_exact_chat().get("code")
            if chat == "EXACT_CHAT_NOT_OPEN" and opt_in_open_chat:
                # One operator-approved navigation, never auto-retry UNKNOWN.
                opened = self.panel.open_exact_chat().get("code")
                if opened not in _GOOD_CHATS:
                    return _blocked("EXACT_CHAT_PRELOAD_" + str(opened),
                                    chrome="VERIFIED_9333")
                chat_preloaded = True
                chat = self.panel.inspect_exact_chat().get("code")
            if chat != "EXACT_CHAT_PRESENT":
                return _blocked("EXACT_CHAT_" + str(chat),
                                chrome="VERIFIED_9333")
            if (self.panel._state() != before or self.panel._route() != binding):
                return _blocked("STATE_OR_ROUTE_CHANGED",
                                chrome="VERIFIED_9333")
            # A second network SHA read is warranted only after a browser
            # mutation (launch/preload); ordinary warm Wake gets one read.
            if (launched or chat_preloaded) and self.source_reader() != before["source_sha"]:
                return _blocked("SOURCE_SHA_CHANGED", chrome="VERIFIED_9333")
        except Exception:
            return _blocked("READINESS_CHECK_UNKNOWN",
                            chrome="VERIFIED_9333")
        return _ready(
            launched=launched,
            verification=acquired.get("verification", "FULL_ACQUISITION"),
            chat_preloaded=chat_preloaded)


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_KEY")
        result[key] = value
    return result


def load_flow_config(path: Path, *, local_app_data: Path) -> dict:
    """A one-time private manifest; no paths or route IDs in output."""
    root = (local_app_data / "AI-Commerce").resolve(strict=True)
    if (not path.is_absolute() or not path.is_file() or path.is_symlink()
            or path.stat().st_size > 8192):
        raise ValueError("FLOW_CONFIG_INVALID")
    path = path.resolve(strict=True)
    if root not in path.parents:
        raise ValueError("FLOW_CONFIG_NOT_PRIVATE_ROOT")
    _private_identity(path)
    raw = json.loads(path.read_text(encoding="utf-8-sig"),
                     object_pairs_hook=_unique_json)
    if (type(raw) is not dict or set(raw) != _FIELDS or
            type(raw["version"]) is not int or raw["version"] != 1):
        raise ValueError("FLOW_CONFIG_INVALID")
    for name in ("checkout", "chrome_config", "state_file", "registry"):
        value = raw[name]
        if type(value) is not str or not Path(value).is_absolute():
            raise ValueError("FLOW_CONFIG_INVALID")
    if (type(raw["route_id"]) is not str or
            not 1 <= len(raw["route_id"]) <= 128 or
            any(ch not in "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in raw["route_id"])):
        raise ValueError("FLOW_CONFIG_INVALID")
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One operator-gated Chrome-to-exact-chat readiness flow; NO Send")
    parser.add_argument("--flow-config", required=True, type=Path)
    parser.add_argument("--opt-in-launch", action="store_true")
    parser.add_argument("--opt-in-open-chat", action="store_true")
    args = parser.parse_args(argv)
    try:
        if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
            raise ValueError("WINDOWS_INTERACTIVE_REQUIRED")
        raw = load_flow_config(args.flow_config,
                               local_app_data=Path(os.environ["LOCALAPPDATA"]))
        checkout = Path(raw["checkout"]).resolve(strict=True)
        if not checkout.is_dir():
            raise ValueError("CHECKOUT_INVALID")
        config = Path(raw["chrome_config"])
        env = chrome_acquisition.load_environment(
            config, checkout, local_app_data=Path(os.environ["LOCALAPPDATA"]))
        if env.port != 9333:
            raise ValueError("CDP_PORT_NOT_9333")
        panel = WakePanel(
            store=TurnSessionStore(path=Path(raw["state_file"]),
                                   repository_root=checkout),
            route_reader=lambda: load_registered_probe_route(
                registry_path=Path(raw["registry"]), checkout=checkout,
                route_id=raw["route_id"]),
        )
        # An out-of-repository owner-checked proof cache survives separate
        # one-shot invocations. It NEVER lets a dead/replaced PID inherit
        # a Chrome proof and never stores private URL/credentials.
        cache = ChromeAttestationCache(
            path=args.flow_config.with_name("wake-readiness-cache.json"),
            checkout=checkout, env=env)
        flow = ReadinessFlow(
            acquire=lambda opt_in: cache.acquire(opt_in_launch=opt_in),
            panel=panel, source_reader=_fresh_main_sha)
        result = flow.execute(
            opt_in_launch=args.opt_in_launch,
            opt_in_open_chat=args.opt_in_open_chat)
    except Exception:
        result = _blocked("FLOW_CONFIG_OR_ENV_INVALID")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "READY_FOR_PREPARE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
