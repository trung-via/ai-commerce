"""Bounded stdin/stdout CLI. No source paths, provider calls or publication."""
import argparse
import sys

from . import (AffiliateAttributionEvidence, AttributionError,
               affiliate_attribution_schema, attribution_source_schema,
               build_attribution_evidence)
from .evidence import MAX_EVIDENCE_BYTES, MAX_SOURCE_BYTES, canonical_bytes
from .schemas import ALGORITHMS


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline L04 supplied evidence replay")
    commands = parser.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("schema")
    schema.add_argument("--source", action="store_true")
    evaluate = commands.add_parser("evaluate", help="Read AffiliateAttributionSource.v1 from stdin")
    evaluate.add_argument("--algorithm", choices=ALGORITHMS, default="UNSPECIFIED")
    evaluate.add_argument("--expected-source-sha256")
    verify = commands.add_parser("verify", help="Fully replay evidence from stdin")
    verify.add_argument("--expected-id")
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            value = attribution_source_schema() if args.source else affiliate_attribution_schema()
            output = canonical_bytes(value)
        elif args.command == "evaluate":
            raw = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
            output = build_attribution_evidence(raw, algorithm=args.algorithm,
                expected_source_sha256=args.expected_source_sha256).to_bytes()
        else:
            raw = sys.stdin.buffer.read(MAX_EVIDENCE_BYTES + 1)
            output = AffiliateAttributionEvidence.from_bytes(raw, expected_id=args.expected_id).to_bytes()
        sys.stdout.buffer.write(output + b"\n")
        return 0
    except (AttributionError, OSError) as exc:
        code = exc.code if isinstance(exc, AttributionError) else "LOCAL_STREAM_ERROR"
        sys.stderr.write(code + "\n")
        return 40


if __name__ == "__main__":
    raise SystemExit(main())
