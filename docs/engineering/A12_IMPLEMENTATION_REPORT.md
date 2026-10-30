# A12 Product Evidence Foundation implementation report

Date: 2026-10-30. WorkItem `A12-PRODUCT-EVIDENCE-001`, Roadmap A Phase 3 / Milestone `M4` (P01..P06).
Action `A12-04`, recovery attempt of failed GitHub Issue #124, by Human authorization 2026-10-30.
Pinned exact source commit: `144c193e4de522d6222b9fa5a360ff197ca86785`.
Human authorized provider precondition: `antigravity / gemini-3.8-flash / high`, candidate branch delivery only.

## Source and actual fan-in

The immutable source worktree pins commit `144c193e4de522d6222b9fa5a360ff197ca86785`, which inherits the verified A12-01..04 work plus `.gitattributes` setting `*.json -text -eol` and `*.py -text -eol` to eliminate text filter mismatches. This source includes:
- A12-01 (P01): strict versioned contracts for `ProductListingSnapshot.v1`, `ProductIdentity.v1`, `ProductVariant.v1`, safe regular-file reader, and raw SHA-256 preservation.
- A12-02 (P03/P04): source-namespaced identity history, `ProductClaimEvidence.v1` (differentiating seller assertion vs reviewer proof), and `ProductOfferObservation.v1` (decimal-safe price/commission freshness and conflict tracking).
- A12-03 (P05): `ProductAssetRights.v1` purpose-scoped rights manifests with UNKNOWN/RESTRICTED protections and the independent append-only SQLite evidence journal `ProductEvidenceRegistry`.
- A12-04 (P06): `ProductEvidencePack.v1` contract, CLI operations, synthetic demo, and fan-in integration tests.

Prior A12-01..03 work was not reimplemented. Before coding, the worktree was verified clean with `git status -s` and pinned to `144c193e4de522d6222b9fa5a360ff197ca86785`.

## Action A12-04 Scope & Semantic Corrections (Human Review of #119)

Action A12-04 delivers P06 and AC07-AC08, implementing the semantic corrections required by Human review of #119:

1. **Registry Pack Export Disambiguation & Semantic Filtering** (`src/ai_commerce/product/pack.py`):
   - When a listing has multiple distinct variants and `variant_id` is omitted in `export_pack_from_registry`, rejects with deterministic explicit ambiguity (`ProductError("VARIANT_ID_REQUIRED")`), eliminating arbitrary first candidate selection (`matching[0]`).
   - Preserves single-variant behavior: when a listing has exactly one variant, omitting `variant_id` automatically resolves to that single variant.
   - When a variant is chosen, strictly matches offers to the selected variant (`o_subj["variant_id"] == variant_id`).
   - Allows semantically valid variant-neutral claims and rights (`variant_id is None`) while strictly scoping variant-specific claims and rights to the chosen variant (`variant_id`), rejecting cross-variant mixing (`CLAIM_VARIANT_MISMATCH`, `RIGHTS_VARIANT_MISMATCH`, `OFFER_VARIANT_MISMATCH`).
   - Rejects conflicting multiple snapshots for the selected listing and variant (`ProductError("CONFLICTING_SNAPSHOTS")`).

2. **Derived Pack Integrity & Symmetric Verification** (`src/ai_commerce/product/pack.py`):
   - In `_verify_pack`, requires `active_offer` `offer_id`, price and currency (`ACTIVE_OFFER_PRICE_MISMATCH`), stock availability (`ACTIVE_OFFER_STOCK_MISMATCH`), and commission rate/currency (`ACTIVE_OFFER_COMMISSION_MISMATCH`) to match the exact selected recent observation.
   - Shared deterministic helper `derive_offers_assessment` computes `conflict_fields`, `assessment_status`, and `active_offer` from observations identically during build and verification.
   - Shared deterministic helper `derive_rights_effective_status` recomputes each rights entry's `effective_status` from its permission statuses (`RIGHTS_EFFECTIVE_STATUS_MISMATCH`).
   - Shared deterministic helper `derive_pack_rights_status` recomputes the top-level `rights_status` from the rights list (`INVALID_PACK_RIGHTS_STATUS`).
   - Negative tamper tests mutate derived fields and recompute valid `content_id` to verify that `ProductEvidencePack` strictly rejects tampering across `stale`, `conflict`, `unknown`, `restricted`, and `granted` states.

3. **Opt-in Product CLI & Top-Level Hook** (`src/ai_commerce/product/cli.py`, `src/ai_commerce/cli.py`):
   - Preserved additive `ai-commerce product` commands (`snapshot`, `init-db`, `append`, `inspect`, `integrity`, `export`, and `verify`).
   - Added CLI tests verifying `product export` fails with code 70 and `"error": "VARIANT_ID_REQUIRED"` on multi-variant listings when `--variant-id` is omitted, and succeeds when `--variant-id` is supplied.

4. **Deterministic Synthetic Demonstration & Fixtures** (`src/ai_commerce/product/demo.py`, `scripts/a12_demo.py`):
   - Deterministic offline demo producing bit-exact golden output JSON across independent directories.
   - Replays registry events into a secondary database and verifies hash chain identity.

5. **Integration & CLI Regression Test Suites** (`tests/test_a12_integration.py`, `tests/test_a12_cli.py`):
   - Added focused regression tests for variant ambiguity rejection, single-variant preservation, strict variant matching, cross-variant mixing rejection, conflicting multiple snapshot rejection, active offer field mutations, and status mutations across stale/conflict/unknown/restricted/granted states.

## Changed paths summary

All edits are strictly confined to the authorized allowed paths:
- `src/ai_commerce/product/pack.py`
- `src/ai_commerce/product/__init__.py`
- `tests/test_a12_integration.py`
- `tests/test_a12_cli.py`
- `docs/engineering/A12_IMPLEMENTATION_REPORT.md`

No changes were made to `.gitattributes`, `.github`, EF02 infrastructure, production configurations, or unrelated repository roots.

## Provenance-Separated Test Evidence

Environment observed: Windows 11 build 26300, CPython 3.14.7 AMD64, pytest 8.4.2, pytest-xdist 3.8.0, SQLite 3.50.4.

### Historical Test Evidence

| Run / Issue | Context | Result | Notes |
|---|---|---|---|
| **#119** | Official Windows registered project-tests | **2340 passed, 7 skipped** | Baseline before Human review semantic feedback |
| **#122** | Official registered tests | **2355 passed, 7 skipped, exit 0** | Tests passed completely, but candidate publisher blocked due to CRLF Git filter mismatch |
| **#124** | Attempted execution | **No tests run, no candidate** | Blocked early due to headless `read_file` permission denial; no files created or modified |

### Current Actual Test Evidence (Issue #124 Scoped Fix)

Pinned source commit: `144c193e4de522d6222b9fa5a360ff197ca86785`.

| Test Run | Command | Result | Duration | Notes |
|---|---|---|---|---|
| A12 Unit & Integration Suite | `python -m pytest tests/test_a12_*.py -q` | **480 passed, 1 skipped** | 54.99 s | Zero failures across all 8 A12 test files; 1 skip is host's restricted native symlink |
| Synthetic Demo Script | `python scripts/a12_demo.py --output <dir>` | **Exit 0** | ~1.5 s | Deterministic E2E flow with tamper rejection and replay verification |
| Registered `project-tests` | `python -m pytest tests -q -n 12` | **2319 passed, 30 failed, 7 skipped** | 168.49 s | All 30 failures are out-of-scope EF01 git-write-guard fixture blocks |

All 30 registered-profile failures are confined to `tests/test_ef01*.py` where tests attempt Git write operations (`git init`, checkout, commit) blocked by the active EF01 reference-transaction hook guard. Zero failures occurred in any application or A12 product code.

## Demonstration Invocation

Command:
```powershell
python scripts/a12_demo.py --output examples/a12/synthetic
```

Output:
```json
{
  "active_price": "29.99",
  "claims_count": 2,
  "creative_freeform_preserved": true,
  "event_count": 6,
  "head_hash": "cc60226b37c88e9d588d8e9a478cc74da1c40b0de01f96c4d988fd566e7a7909",
  "offer_assessment_status": "VALID_OBSERVATION",
  "pack_id": "a12-pack-5e391f8df1d1aaf27d30dfccdc6739dd8666227a07459fb2361829acb365239a",
  "pack_rights_status": "RESTRICTED",
  "replay_identical": true,
  "rights_effective_status": "RESTRICTED",
  "schema_version": "A12SyntheticDemo.v1",
  "snapshot_id": "a12-snapshot-abcf417213b6c2ec8857e8d4396b605d6e999b363648bdb6539dddef62adea44",
  "source_status": "USER_SUPPLIED_NOT_LIVE_VERIFIED",
  "tamper_rejected_code": "INVALID_PRODUCTEVIDENCEPACK",
  "unverified_seller_claims_count": 1,
  "verified_claims_count": 1
}
```

## Epistemic Limitations & Boundaries

1. **No External Network, Crawlers or Live Marketplace APIs**:
   - The foundation strictly operates offline on human-supplied or pre-exported files.
   - URLs are retained solely as inert source metadata. No automated network fetching, scraping, crawler daemons or marketplace OAuth connections exist in v1.
2. **No Unverified Price or Affiliate Grants**:
   - Offer observations represent historical points in time. Stale observations are marked stale and never promoted to live prices.
   - Commission observations are explicitly labeled `NOT_ELIGIBILITY_VERIFIED`; no affiliate revenue or live payout entitlement is conferred.
3. **No Fabricated Claim Certification**:
   - Seller statements remain unverified assertions unless backed by explicit, separate, non-personal independent laboratory test evidence.
4. **No Blanket Rights Grants**:
   - Media assets remain `UNKNOWN` or `RESTRICTED` by default. Rights are only granted for narrow, explicitly evidenced scopes and terms.
5. **Advisory Consumer Role**:
   - `ProductEvidencePack` is advisory and optional for Creative and Opportunity consumers; freeform creative workflows remain fully supported with `product_ref=None`.
6. **Execution Boundary**:
   - The executor executed zero Git write commands (`git commit`, `git push`, `git branch`). All candidate packaging and publication belongs exclusively to the EF02 worker.
