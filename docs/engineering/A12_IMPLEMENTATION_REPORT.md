# A12 Product Evidence Foundation implementation report

Date: 2026-10-30. WorkItem `A12-PRODUCT-EVIDENCE-001`, Roadmap A Phase 3 / Milestone `M4` (P01..P06).
Action `A12-04`, recovery attempt `a12-04-antigravity38flashhigh-20261030-v1-05` (Issue #116, replacing prior Codex quota timeout).
Human authorized provider precondition: `antigravity / gemini-3.8-flash / high`, candidate branch delivery only.

## Source and actual fan-in

The immutable packet pins the verified A12-03 candidate `09f46135f68c748a0074a8ef4a6a5a2f90afa8a7` published on candidate branch `refs/heads/ef02/candidate/8d72a34a55f1c22d840f7c87cf005c4a`, inheriting from source commit `5e17381b5cc1d0ef05a56de0138a3972bc524169`. This immutable source includes verified work from:
- A12-01 (P01): strict versioned contracts for `ProductListingSnapshot.v1`, `ProductIdentity.v1`, `ProductVariant.v1`, safe regular-file reader, and raw SHA-256 preservation.
- A12-02 (P03/P04): source-namespaced identity history, `ProductClaimEvidence.v1` (differentiating seller assertion vs reviewer proof), and `ProductOfferObservation.v1` (decimal-safe price/commission freshness and conflict tracking).
- A12-03 (P05): `ProductAssetRights.v1` purpose-scoped rights manifests with UNKNOWN/RESTRICTED protections and the independent append-only SQLite evidence journal `ProductEvidenceRegistry`.

Prior A12-01..03 work was not reimplemented. Before coding, the candidate worktree was verified clean with `git status -s`. Initial baseline tests on inherited A12 test suites passed: **455 passed, 1 skipped** (native symlink skip).

## Action A12-04 Scope & Implementation

Action A12-04 delivers P06 and AC07-AC08, providing fan-in integration across all A12 candidate work:

1. **ProductEvidencePack.v1 Contract & Python Read Model** (`contracts/ProductEvidencePack.v1.schema.json`, `src/ai_commerce/product/pack_schemas.py`, `src/ai_commerce/product/pack.py`):
   - Strict versioned JSON schema mirrored 100% byte-for-byte by `schema("ProductEvidencePack")`.
   - Content ID addressed as `a12-pack-[a-f0-9]{64}`, hashing canonical UTF-8 JSON excluding `pack_id`.
   - Synthesizes exact listing snapshot source evidence, variant identity, claims, offers with freshness evaluation, asset rights manifests, and human advisory caveats.
   - Preserves source-observed claims and seller assertions; strictly forbids upgrading seller claims to `INDEPENDENTLY_VERIFIED_ONLY_WITH_REAL_EVIDENCE` without separate reviewer proof.
   - Evaluates offer prices, stock, commission, staleness (`STALE` / `EXPIRED` / `MAX_AGE_EXCEEDED`), and detects concurrent pricing conflicts (`assessment_status="CONFLICT"`).
   - Preserves media asset rights with `UNKNOWN` and `RESTRICTED` protections; only permits `GRANTED` for purposes with verified local license instruments.
   - Embeds six core human advisory explanations in `advisory_caveats`.

2. **Opt-in Product CLI & Top-Level Hook** (`src/ai_commerce/product/cli.py`, `src/ai_commerce/cli.py`):
   - Additive `ai-commerce product` command supporting `snapshot`, `init-db`, `append`, `inspect`, `integrity`, `export`, and `verify`.
   - Preserves existing CLI commands (`hello`, `about`, `validate`, `concept-*`, `asset-*`, `composition-*`, `render-*`, `creative-e2e`, `channel-capability`, `distribution`, `analytics`, `learning`, `opportunity`).
   - Strict error convention: valid commands exit 0 and output JSON to stdout; invalid inputs or `ProductError` exceptions output JSON on stderr and exit 70.

3. **Deterministic Synthetic Demonstration & Fixtures** (`src/ai_commerce/product/demo.py`, `scripts/a12_demo.py`, `examples/a12/`):
   - Standalone script `scripts/a12_demo.py --output <dir>` runs end-to-end import -> identity -> claims/offers -> rights -> append-only registry -> pack export -> replay and tamper verification.
   - Produces stable deterministic output JSON across independent directories.
   - Replays registry events into a secondary database and verifies exact hash chain identity.
   - Verifies Creative intake: brief referencing `product_ref = pack.pack_id` passes, while freeform creative briefs (`product_ref=None`) remain completely supported.

4. **Integration & CLI Verification Test Suites** (`tests/test_a12_integration.py`, `tests/test_a12_cli.py`):
   - `test_a12_integration.py`: 11 comprehensive tests covering schema parity, contract sealing, full pipeline replay, demo determinism, Creative intake, negative tamper rejection, stale offer freshness, pricing conflicts, variant isolation, and advisory caveats.
   - `test_a12_cli.py`: 5 tests covering CLI help, snapshot import, database initialization, record append, inspection, integrity check, pack export, verification, negative error codes, and existing CLI command preservation.

## Changed paths summary

All edits are strictly confined to the authorized allowed paths:
- `contracts/ProductEvidencePack.v1.schema.json`
- `src/ai_commerce/product/pack_schemas.py`
- `src/ai_commerce/product/pack.py`
- `src/ai_commerce/product/cli.py`
- `src/ai_commerce/product/demo.py`
- `src/ai_commerce/product/schemas.py`
- `src/ai_commerce/product/core.py`
- `src/ai_commerce/product/__init__.py`
- `src/ai_commerce/cli.py`
- `examples/a12/`
- `scripts/a12_demo.py`
- `tests/test_a12_integration.py`
- `tests/test_a12_cli.py`
- `docs/engineering/A12_IMPLEMENTATION_REPORT.md`

No changes were made to `.github`, EF02 infrastructure, production configurations, or unrelated repository roots.

## Observed Verification Results

Environment observed: Windows 11 build 26300, CPython 3.14.7 AMD64, pytest 8.4.2, pytest-xdist 3.8.0, SQLite 3.50.4.

| Test Run | Command | Result | Duration | Notes |
|---|---|---|---|---|
| A12 Unit & Integration Suite | `python -m pytest tests/test_a12_*.py -q -n 12` | **471 passed, 1 skipped** | 17.91 s | Zero failures across all 8 A12 test files; 1 skip is host's restricted native symlink |
| Synthetic Demo Script | `python scripts/a12_demo.py --output examples/a12/synthetic` | **Exit 0** | ~1.5 s | Full deterministic E2E flow with tamper rejection and replay verification |
| Registered `project-tests` | `python -m pytest tests -q -n 12` | **2310 passed, 30 failed, 7 skipped** | 137.46 s | All 30 failures are out-of-scope EF01 git-write-guard fixture blocks |

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
