"""A13-E03 Affiliate Release CLI: offline advisory evidence pack evaluation, verification, and demo.

Strictly offline; never performs OAuth, network calls, link activation, or publishing.
"""
from __future__ import annotations

import argparse
import json
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ai_commerce.product import ProductError, parse_json

from .claim_rights_schemas import claim_rights_review_schema
from .demo import run_demo
from .evidence_pack import (
    AffiliateReleaseError,
    build_affiliate_release_pack,
    read_affiliate_release_pack,
    write_affiliate_release_pack,
)
from .evidence_pack_schemas import affiliate_release_pack_schema
from .placement_schemas import affiliate_placement_schema
from .schemas import media_inspection_schema


def _configure_subparsers(operations) -> None:
    # schema
    sch = operations.add_parser("schema", help="Export self-contained Draft 2020-12 JSON schema")
    sch.add_argument(
        "--contract",
        choices=("pack", "placement", "claim-rights", "media-inspection"),
        default="pack",
    )
    sch.add_argument("--output", type=Path, help="Optional output file")

    # evaluate / build / review
    for cmd_name in ("evaluate", "build", "review"):
        ev = operations.add_parser(
            cmd_name,
            help="Build and evaluate an offline advisory AffiliateReleaseEvidencePack.v1",
        )
        ev.add_argument("--request", type=Path, help="JSON request file with scope/temporal/disclosure fields")
        ev.add_argument("--platform", choices=("TIKTOK", "FACEBOOK", "INSTAGRAM", "YOUTUBE"))
        ev.add_argument("--account", help="account_ref as namespace:id or id")
        ev.add_argument("--app", help="app_ref as namespace:id or id")
        ev.add_argument("--market", help="2-letter ISO market code")
        ev.add_argument("--territory", help="2-letter ISO territory code")
        ev.add_argument("--surface", help="Placement surface (e.g. PRODUCT_ANCHOR)")
        ev.add_argument("--channel", help="Rights channel alias (e.g. social)")
        ev.add_argument("--product-id", dest="product_id")
        ev.add_argument("--listing-id", dest="listing_id")
        ev.add_argument("--variant-id", dest="variant_id")
        ev.add_argument("--release-sha256", dest="release_sha256")
        ev.add_argument("--link-url", dest="link_url")
        ev.add_argument("--as-of", dest="as_of")
        ev.add_argument("--valid-from", dest="valid_from")
        ev.add_argument("--use-from", dest="use_from")
        ev.add_argument("--use-until", dest="use_until")
        ev.add_argument("--valid-until", dest="valid_until")
        ev.add_argument("--revoked-at", dest="revoked_at")
        ev.add_argument("--disclosure", help="Disclosure statement text or JSON file path")
        ev.add_argument("--media-inspection", type=Path, dest="media_inspection")
        ev.add_argument("--claim-rights-review", type=Path, dest="claim_rights_review")
        ev.add_argument("--placement-evidence", type=Path, dest="placement_evidence")
        ev.add_argument("--product-pack", type=Path, dest="product_pack")
        ev.add_argument("--snapshot", type=Path)
        ev.add_argument("--snapshot-raw", type=Path, dest="snapshot_raw")
        ev.add_argument("--offer-observation", type=Path, dest="offer_observation")
        ev.add_argument("--capability-decision", type=Path, dest="capability_decision")
        ev.add_argument("--capability-snapshot", type=Path, dest="capability_snapshot")
        ev.add_argument("--capability-db", type=Path, dest="capability_db")
        ev.add_argument("--distribution-package", type=Path, dest="distribution_package")
        ev.add_argument("--output", type=Path, help="Optional destination JSON file")

    # verify / inspect
    for cmd_name in ("verify", "inspect"):
        ver = operations.add_parser(
            cmd_name,
            help="Strictly verify and load an AffiliateReleaseEvidencePack.v1 JSON file",
        )
        ver.add_argument("--file", type=Path, required=True)
        ver.add_argument("--expected-id", dest="expected_id", default=None)

    # demo
    dm = operations.add_parser("demo", help="Run deterministic offline A13-E03 demonstration")
    dm.add_argument("--output", type=Path, required=True, help="New output directory")


def add_parser(commands) -> None:
    parser = commands.add_parser(
        "affiliate-release",
        help="A13-E03 offline advisory affiliate release evidence pack",
    )
    operations = parser.add_subparsers(dest="affiliate_release_operation", required=True)
    _configure_subparsers(operations)


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="ai-commerce-affiliate-release",
        description="A13-E03 offline advisory AffiliateReleaseEvidencePack.v1 CLI",
    )
    operations = parser.add_subparsers(dest="affiliate_release_operation", required=True)
    _configure_subparsers(operations)
    return parser


def _parse_ref(raw: str | None) -> dict[str, str] | str | None:
    if raw is None:
        return None
    if ":" in raw:
        ns, ident = raw.split(":", 1)
        return {"namespace": ns, "id": ident}
    return raw


def _read_optional_bytes(path: Path | None) -> bytes | None:
    if path is None:
        return None
    if not path.is_file() or path.is_symlink():
        raise AffiliateReleaseError("INPUT_FILE_INVALID")
    return path.read_bytes()


def run(args: Namespace) -> int:
    try:
        op = args.affiliate_release_operation
        if op == "schema":
            mapping = {
                "pack": affiliate_release_pack_schema,
                "placement": affiliate_placement_schema,
                "claim-rights": claim_rights_review_schema,
                "media-inspection": media_inspection_schema,
            }
            result = mapping[args.contract]()
            if args.output:
                if args.output.exists():
                    raise AffiliateReleaseError("SCHEMA_OUTPUT_ALREADY_EXISTS")
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
                    encoding="utf-8",
                )
        elif op in ("evaluate", "build", "review"):
            req_data: dict[str, Any] | None = None
            if args.request is not None:
                req_bytes = _read_optional_bytes(args.request)
                parsed_req = parse_json(req_bytes)
                if not isinstance(parsed_req, dict):
                    raise AffiliateReleaseError("INVALID_RELEASE_REQUEST")
                req_data = parsed_req

            disc_arg: Any = getattr(args, "disclosure", None)
            if isinstance(disc_arg, str) and disc_arg.endswith(".json") and Path(disc_arg).is_file():
                disc_value: Any = parse_json(Path(disc_arg).read_bytes())
            elif disc_arg is not None:
                disc_value = disc_arg
            elif req_data is not None and "disclosure" in req_data:
                disc_value = req_data["disclosure"]
            else:
                disc_value = None

            dist_pkg: Any = None
            if args.distribution_package is not None:
                if args.distribution_package.is_dir():
                    dist_pkg = args.distribution_package
                else:
                    dist_pkg = _read_optional_bytes(args.distribution_package)

            pack = build_affiliate_release_pack(
                request=req_data,
                platform=args.platform,
                account_ref=_parse_ref(args.account),
                app_ref=_parse_ref(args.app),
                market=args.market,
                territory=args.territory,
                surface=args.surface,
                channel=args.channel,
                product_id=args.product_id,
                listing_id=args.listing_id,
                variant_id=args.variant_id,
                release_sha256=args.release_sha256,
                link_url=args.link_url,
                as_of=args.as_of,
                valid_from=args.valid_from,
                use_from=args.use_from,
                use_until=args.use_until,
                valid_until=args.valid_until,
                revoked_at=args.revoked_at,
                disclosure=disc_value,
                media_inspection=_read_optional_bytes(args.media_inspection),
                claim_rights_review=_read_optional_bytes(args.claim_rights_review),
                placement_evidence=_read_optional_bytes(args.placement_evidence),
                product_pack=_read_optional_bytes(args.product_pack),
                snapshot=_read_optional_bytes(args.snapshot),
                snapshot_raw=_read_optional_bytes(args.snapshot_raw),
                offer_observation=_read_optional_bytes(args.offer_observation),
                capability_decision=_read_optional_bytes(args.capability_decision),
                capability_snapshot=_read_optional_bytes(args.capability_snapshot),
                capability_registry=args.capability_db,
                distribution_package=dist_pkg,
            )
            if args.output:
                write_affiliate_release_pack(args.output, pack, overwrite=False)
            result = pack.to_dict()
        elif op in ("verify", "inspect"):
            pack = read_affiliate_release_pack(args.file, expected_id=args.expected_id)
            result = pack.to_dict()
        elif op == "demo":
            result = run_demo(args.output)
        else:
            raise AffiliateReleaseError("UNKNOWN_AFFILIATE_RELEASE_OPERATION")

        print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return 0
    except AffiliateReleaseError as exc:
        print(json.dumps({"error": exc.code}, ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 73
    except ProductError as exc:
        print(json.dumps({"error": "A12_" + exc.code}, ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 73
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        print(
            json.dumps({"error": "AFFILIATE_RELEASE_INPUT_OR_IO_ERROR"}, ensure_ascii=True, sort_keys=True),
            file=sys.stderr,
        )
        return 73


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args)
