"""Bounded stdin/stdout snapshot verification; no network or filesystem writes."""
import json
import sys

from . import AffiliateCommerceRun, CommerceError, affiliate_commerce_run_schema
from .service import MAX_BYTES


def main():
    if sys.argv[1:] == ["schema"]:
        print(json.dumps(affiliate_commerce_run_schema(), sort_keys=True))
        return 0
    if sys.argv[1:] != ["verify"]:
        print('{"error_code":"EXPECTED_SCHEMA_OR_VERIFY"}', file=sys.stderr)
        return 2
    try:
        result = AffiliateCommerceRun.from_bytes(sys.stdin.buffer.read(MAX_BYTES + 1))
        sys.stdout.buffer.write(result.to_bytes() + b"\n")
        return 0
    except CommerceError as exc:
        print(json.dumps({"error_code": exc.code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
