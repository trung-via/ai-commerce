"""Regression: raw A11/A12 EF02 evidence JSON remains byte-identical across Git checkouts.

#106 failed before Codex because Git for Windows normalized two CRLF JSON blobs
using '*.json text eol=lf'; original Git blobs are immutable evidence.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RAW_EVIDENCE = (
    "examples/a11/evidence/project-tests-pre-normalization-result.json",
    "examples/a11/evidence/project-tests-result.json",
)
FUTURE_EVIDENCE = "examples/a12/evidence/project-tests-result.json"


def _git(*args: str) -> bytes:
    p = subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, check=True
    )
    return p.stdout


def _attributes(*paths: str) -> dict[tuple[str, str], str]:
    # -z is unambiguous for paths containing spaces.
    raw = _git("check-attr", "-z", "text", "eol", "--", *paths)
    parts = raw.split(b"\0")
    assert parts[-1] == b""
    triples = list(zip(parts[::3], parts[1::3], parts[2::3]))
    return {(p.decode(), a.decode()): v.decode() for p, a, v in triples}


def test_evidence_blob_attributes_preserve_raw_bytes() -> None:
    paths = (*RAW_EVIDENCE, FUTURE_EVIDENCE)
    attrs = _attributes(*paths)
    for path in paths:
        assert attrs[(path, "text")] == "unset"
        assert attrs[(path, "eol")] == "unset"

    for path in RAW_EVIDENCE:
        raw = (REPO / path).read_bytes()
        # Protect these two specific CRLF source blobs; don't rewrite or rehash.
        assert b"\r\n" in raw
        tracked = _git("ls-files", "--stage", "--", path).decode().split()[1]
        actual = _git("hash-object", "--no-filters", "--", path).decode().strip()
        assert actual == tracked
