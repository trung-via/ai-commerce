"""One-time, operator-private D5 readiness manifest enrollment (NO browser Send).

Only the logged-in Windows operator can provision this under their existing
SID/DACL-protected LocalAppData directory. This code does not create a ledger,
grant route consent, alter Chrome profiles, launch Chrome, or authorize Wake.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from . import chrome_acquisition
from ._private_state import PrivateJsonState
from .local_completion_handoff import _private_identity
from .plugin_selection_probe_cli import load_registered_probe_route
from .plugin_turn_cli import _fresh_main_sha
from .plugin_turn_store import TurnSessionStore
from .wake_readiness_flow import load_flow_config


class SetupBlocked(ValueError):
    """Sanitized reason code; never print route URLs or private filesystem paths."""


def _private_root(local_app_data: Path) -> Path:
    try:
        appdata = local_app_data.resolve(strict=True)
        root = appdata / "AI-Commerce"
        if not root.is_dir() or root.is_symlink() or root.resolve(strict=True) != root:
            raise SetupBlocked("PRIVATE_ROOT_NOT_PROVISIONED")
        # Existing SID+DACL guard; this function raises if shared/untrusted.
        _private_identity(root)
        return root
    except SetupBlocked:
        raise
    except Exception:
        raise SetupBlocked("PRIVATE_ROOT_NOT_TRUSTED") from None


def _same_loopback_port(url: str, expected_port: int) -> bool:
    if type(expected_port) is not int or expected_port not in (9333, 9444):
        return False
    try:
        u = urlsplit(url)
        return (u.scheme == "http" and u.netloc == f"127.0.0.1:{expected_port}"
                and u.port == expected_port and not u.username and not u.password
                and u.path in ("", "/") and not u.query and not u.fragment)
    except (ValueError, TypeError, AttributeError):
        return False


def _same_loopback_9333(url: str) -> bool:
    # Preserve historical 9333 config; never migrate its Chrome profile.
    return _same_loopback_port(url, 9333)


def _checkout_sha(checkout: Path) -> str:
    """Direct read-only Git identity, without network or checkout mutation."""
    try:
        def git(*args):
            result = subprocess.run(["git", "-C", str(checkout), *args],
                                    capture_output=True, text=True, timeout=8,
                                    check=False)
            if result.returncode:
                raise SetupBlocked("CHECKOUT_GIT_UNAVAILABLE")
            return result.stdout.strip()
        if Path(git("rev-parse", "--show-toplevel")).resolve(strict=True) != checkout:
            raise SetupBlocked("CHECKOUT_ROOT_MISMATCH")
        sha = git("rev-parse", "--verify", "HEAD")
        if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise SetupBlocked("CHECKOUT_GIT_UNAVAILABLE")
        return sha
    except SetupBlocked:
        raise
    except Exception:
        raise SetupBlocked("CHECKOUT_GIT_UNAVAILABLE") from None


def provision(*, checkout: Path, chrome_config: Path, state_file: Path,
              registry: Path, route_id: str, local_app_data: Path,
              target_cdp_port: int = 9333,
              source_reader=_fresh_main_sha, checkout_reader=_checkout_sha,
              owner_reader=_private_identity,
              chrome_loader=chrome_acquisition.load_environment,
              route_loader=load_registered_probe_route,
              store_cls=TurnSessionStore) -> dict:
    """Create exactly one immutable private manifest after live contract checks.

    Repeated enrollment of byte-for-byte identical parameters is idempotent.
    Existing different or untrusted manifest is never overwritten.
    """
    try:
        if type(target_cdp_port) is not int or target_cdp_port not in (9333, 9444):
            raise SetupBlocked("CDP_PORT_NOT_SUPPORTED")
        root = _private_root(local_app_data)
        checkout = checkout.resolve(strict=True)
        if not checkout.is_dir():
            raise SetupBlocked("CHECKOUT_INVALID")
        env = chrome_loader(chrome_config, checkout, local_app_data=local_app_data)
        if env.port != target_cdp_port or not _same_loopback_port(env.endpoint, target_cdp_port):
            raise SetupBlocked("CONFIG_NOT_EXACT_CDP_" + str(target_cdp_port))
        if not route_id or len(route_id) > 128:
            raise SetupBlocked("ROUTE_ID_INVALID")
        binding = route_loader(
            registry_path=registry, checkout=checkout, route_id=route_id)
        if not _same_loopback_port(binding.cdp_endpoint, target_cdp_port):
            raise SetupBlocked("ENROLLED_ROUTE_NOT_" + str(target_cdp_port))
        ledger = store_cls(path=state_file, repository_root=checkout)
        session = ledger.read()
        if session["phase"] == "SUBMISSION_UNKNOWN":
            raise SetupBlocked("UNKNOWN_REQUIRES_RECONCILIATION")
        if session["phase"] != "READY":
            raise SetupBlocked("FRESH_READY_LEDGER_REQUIRED")
        if session["source_sha"] != source_reader():
            raise SetupBlocked("SOURCE_SHA_CHANGED")
        if checkout_reader(checkout) != session["source_sha"]:
            raise SetupBlocked("CHECKOUT_SHA_NOT_CURRENT")
        # Do not accept relative paths, symlinks, or source files inside repo.
        paths = {
            "checkout": checkout,
            "chrome_config": chrome_config,
            "state_file": state_file,
            "registry": registry,
        }
        for name, path in paths.items():
            if not path.is_absolute() or path.is_symlink():
                raise SetupBlocked("PRIVATE_INPUT_LOCATION_INVALID")
            if name != "checkout":
                resolved = path.resolve(strict=True)
                if resolved == checkout or checkout in resolved.parents:
                    raise SetupBlocked("PRIVATE_INPUT_INSIDE_REPO")
                if owner_reader(resolved) != owner_reader(root):
                    raise SetupBlocked("PRIVATE_INPUT_OWNER_MISMATCH")
        manifest = {
            "version": 1, "checkout": str(checkout),
            "chrome_config": str(chrome_config.resolve(strict=True)),
            "state_file": str(state_file.resolve(strict=True)),
            "registry": str(registry.resolve(strict=True)),
            "route_id": route_id,
        }
        # A 9444 manifest never overwrites or silently reuses legacy 9333.
        destination = root / ("wakehub-readiness-9444.json" if target_cdp_port == 9444
                              else "wake-readiness.json")
        if destination.exists() or destination.is_symlink():
            current = load_flow_config(destination, local_app_data=local_app_data)
            if current != manifest:
                raise SetupBlocked("MANIFEST_ALREADY_ENROLLED_DIFFERENT")
            return {"status": "ALREADY_CONFIGURED", "wake_sent": False,
                    "browser_launched": False}
        principal = owner_reader(root)
        state = PrivateJsonState(destination, checkout, label="D5_WAKE_SETUP",
                                 max_bytes=8192)
        with state.locked(new_file_owner_sid=principal if os.name == "nt" else None) as tx:
            existing = tx.read({})
            if existing:
                raise SetupBlocked("MANIFEST_CONFLICT")
            tx.write(manifest)
        if load_flow_config(destination, local_app_data=local_app_data) != manifest:
            raise SetupBlocked("MANIFEST_VERIFY_FAILED")
        return {"status": "CONFIGURED", "wake_sent": False,
                "browser_launched": False}
    except SetupBlocked:
        raise
    except Exception:
        # No paths, private route/chat identities or data in public output.
        raise SetupBlocked("OPERATOR_PRIVATE_SETUP_UNAVAILABLE") from None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="D5 one-time private setup; NO Chrome launch or Send")
    p.add_argument("--checkout", type=Path, required=True)
    p.add_argument("--chrome-config", type=Path, required=True)
    p.add_argument("--state-file", type=Path, required=True)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--route-id", required=True)
    p.add_argument("--cdp-port", type=int, choices=(9333, 9444), default=9333)
    args = p.parse_args(argv)
    try:
        if os.name != "nt" or "LOCALAPPDATA" not in os.environ:
            raise SetupBlocked("WINDOWS_OPERATOR_REQUIRED")
        result = provision(
            checkout=args.checkout, chrome_config=args.chrome_config,
            state_file=args.state_file, registry=args.registry,
            route_id=args.route_id, local_app_data=Path(os.environ["LOCALAPPDATA"]),
            target_cdp_port=args.cdp_port)
        print(json.dumps(result, sort_keys=True))
        return 0
    except SetupBlocked as exc:
        print(json.dumps({"status": "BLOCKED", "code": str(exc),
                          "wake_sent": False, "browser_launched": False},
                         sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
