"""Installed entry point: run python -I -B with an absolute reviewed release path.

The installer protects THIS file and the interpreter with Windows ACLs. Do not
run a copy from a PR worktree under the broker or transport account.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ai_commerce.hostops_v3.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
