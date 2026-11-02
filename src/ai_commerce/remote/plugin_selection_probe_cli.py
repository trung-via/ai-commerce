"""Operator-invoked read-only plugin-chip probe for an enrolled local Test Wake Chat.

A fresh EF02 event is NOT required for composer reconnaissance. This module
cannot send Wake, enroll a new route, grant origin authority or mutate D2.
It emits only allowlisted status codes and plugin-selection candidates.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .conversation_binding import (
    ResolvedBinding, _CHAT, _ROUTE, _decode_unique, _endpoint, _outside_repo,
)
from .plugin_selection_probe import ReadOnlyPluginSelectionProbe

_MAX_REGISTRY = 131072


class LocalProbeConfigError(ValueError):
    """Never echo the private registry, route URL or CDP endpoint."""


def load_registered_probe_route(*, registry_path: Path, checkout: Path,
                                route_id: str) -> ResolvedBinding:
    """Load ONLY a pre-enrolled consented route; never infer the active tab."""
    if not isinstance(route_id, str) or not _ROUTE.fullmatch(route_id):
        raise LocalProbeConfigError("PROBE_ROUTE_INVALID")
    try:
        root = checkout.resolve(strict=True)
        registry = Path(registry_path)
        if (not root.is_dir() or not registry.is_absolute() or
                not _outside_repo(registry, root) or
                not registry.is_file() or registry.stat().st_size > _MAX_REGISTRY):
            raise LocalProbeConfigError("PROBE_REGISTRY_INVALID")
        raw = registry.read_bytes()
        if len(raw) > _MAX_REGISTRY:
            raise LocalProbeConfigError("PROBE_REGISTRY_INVALID")
        data = _decode_unique(raw.decode("utf-8-sig"))
    except LocalProbeConfigError:
        raise
    except Exception:
        raise LocalProbeConfigError("PROBE_REGISTRY_UNAVAILABLE") from None
    if (not isinstance(data, dict) or set(data) != {"version", "routes", "claims"} or
            type(data["version"]) is not int or data["version"] != 1 or
            not isinstance(data["routes"], list) or
            not 1 <= len(data["routes"]) <= 128 or
            not isinstance(data["claims"], list) or len(data["claims"]) > 256):
        raise LocalProbeConfigError("PROBE_REGISTRY_INVALID")
    selected = []
    seen_ids, seen_chats = set(), set()
    for item in data["routes"]:
        if (not isinstance(item, dict) or set(item) != {
                "route_id", "generation", "chat_url", "cdp_endpoint", "consent"} or
                not isinstance(item["route_id"], str) or
                not _ROUTE.fullmatch(item["route_id"]) or
                type(item["generation"]) is not int or item["generation"] < 1 or
                type(item["consent"]) is not bool or
                not isinstance(item["chat_url"], str) or
                not _CHAT.fullmatch(item["chat_url"]) or
                not _endpoint(item["cdp_endpoint"])):
            raise LocalProbeConfigError("PROBE_REGISTRY_INVALID")
        chat_id = _CHAT.fullmatch(item["chat_url"]).group(1)
        if item["route_id"] in seen_ids or chat_id in seen_chats:
            raise LocalProbeConfigError("PROBE_REGISTRY_AMBIGUOUS")
        seen_ids.add(item["route_id"])
        seen_chats.add(chat_id)
        if item["route_id"] == route_id and item["consent"] is True:
            selected.append(item)
    if len(selected) != 1:
        raise LocalProbeConfigError("PROBE_ROUTE_NOT_CONSENTED")
    item = selected[0]
    return ResolvedBinding(
        route_id=route_id, generation=item["generation"],
        chat_url=item["chat_url"], cdp_endpoint=item["cdp_endpoint"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only plugin-chip scan of ONE pre-enrolled Test Wake Chat; never Send.")
    parser.add_argument("--registry", required=True, type=Path,
                        help="Private local D5 route registry, outside the repository")
    parser.add_argument("--checkout", required=True, type=Path,
                        help="Local repository root for private-registry boundary check")
    parser.add_argument("--route-id", required=True,
                        help="Existing operator-enrolled route ID; never guessed")
    parser.add_argument("--read-only", action="store_true",
                        help="Required explicit one-shot permission to attach and inspect")
    args = parser.parse_args(argv)
    if not args.read_only or os.name != "nt":
        print(json.dumps({"code": "PROBE_DISABLED", "host_selection_proven": False}))
        return 2
    try:
        binding = load_registered_probe_route(
            registry_path=args.registry, checkout=args.checkout, route_id=args.route_id)
    except LocalProbeConfigError as exc:
        print(json.dumps({"code": str(exc), "host_selection_proven": False}))
        return 2
    result = ReadOnlyPluginSelectionProbe(enabled=True).inspect(binding)
    print(json.dumps({
        "code": result.code,
        "brain": result.brain,
        "engineering": result.engineering,
        "host_selection_proven": False,
    }, sort_keys=True))
    return 0 if result.code == "PROBE_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
