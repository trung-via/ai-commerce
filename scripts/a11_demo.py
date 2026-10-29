"""Run a deterministic offline synthetic A11 example in a new directory."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_commerce.learning.demo import run_demo


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True, help="New directory; never overwrites an existing demo")
    args = parser.parse_args()
    print(json.dumps(run_demo(args.output), sort_keys=True, allow_nan=False))
