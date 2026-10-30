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


def test_repair_evidence_json_is_raw_and_unfiltered() -> None:
    """A12-01 emits literal CRLF JSON under repair_evidence during EF01-R.

    Its verified candidate is later checked out by EF02 for A12-02;
    the Git blob must not become falsely dirty after Windows checkout.
    """
    paths = (
        "src/ai_commerce/product/repair_evidence/round1.failures.json",
        "src/ai_commerce/product/repair_evidence/round1.final-source.json",
        "src/ai_commerce/product/repair_evidence/round1.project-tests.json",
        "src/ai_commerce/product/repair_evidence/round1.repro.json",
        "src/ai_commerce/product/repair_evidence/round1.static.json",
        "src/ai_commerce/product/repair_evidence/round1.targeted.json",
        "src/ai_commerce/learning/repair_evidence/round2.project-tests.json",
    )
    attrs = _attributes(*paths)
    for path in paths:
        assert attrs[(path, "text")] == "unset"
        assert attrs[(path, "eol")] == "unset"

    # Non-evidence JSON is deliberately still LF-normalized by policy.
    normal = _attributes("contracts/ProductIdentity.v1.schema.json")
    assert normal[("contracts/ProductIdentity.v1.schema.json", "text")] == "unset"
    assert normal[("contracts/ProductIdentity.v1.schema.json", "eol")] == "unset"
    generic = _attributes("contracts/SignalImport.v1.schema.json")
    assert generic[("contracts/SignalImport.v1.schema.json", "text")] == "set"
    assert generic[("contracts/SignalImport.v1.schema.json", "eol")] == "lf"


def test_action_specific_evidence_is_raw_and_unfiltered() -> None:
    """Avoid repeating #106/#111 when an action creates its own *_evidence dir.

    The verified A12-02 candidate publishes CRLF JSON files in this folder;
    the dependent A12-03 must start from a clean Git for Windows checkout.
    """
    paths = (
        "src/ai_commerce/product/a12_02_evidence/final-source.json",
        "src/ai_commerce/product/a12_02_evidence/initial.targeted.json",
        "src/ai_commerce/product/a12_02_evidence/project-tests.failures.json",
        "src/ai_commerce/product/a12_02_evidence/project-tests.json",
        "src/ai_commerce/product/a12_02_evidence/review1.targeted.json",
        "src/ai_commerce/product/a12_02_evidence/schema-review.json",
        "src/ai_commerce/product/a12_02_evidence/static.json",
        "src/ai_commerce/product/a12_03_evidence/project-tests.json",
        "src/ai_commerce/learning/learning_evidence/future-result.json",
    )
    attrs = _attributes(*paths)
    for path in paths:
        assert attrs[(path, "text")] == "unset"
        assert attrs[(path, "eol")] == "unset"


def test_product_v1_contract_blobs_preserve_bytes_without_being_changed_by_checkout() -> None:
    """A12-02 published two CRLF Product v1 schemas with immutable Git blobs.

    These contracts are semantically schema validated, but must not appear as
    modified merely because a dependent action checks out an A12 candidate.
    """
    paths = (
        "contracts/ProductClaimEvidence.v1.schema.json",
        "contracts/ProductOfferObservation.v1.schema.json",
        "contracts/ProductIdentity.v1.schema.json",
        "contracts/ProductAssetRights.v1.schema.json",
        "contracts/ProductEvidencePack.v1.schema.json",
    )
    attrs = _attributes(*paths)
    for path in paths:
        assert attrs[(path, "text")] == "unset"
        assert attrs[(path, "eol")] == "unset"
