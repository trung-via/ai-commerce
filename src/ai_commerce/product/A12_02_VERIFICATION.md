# A12-02 local implementation — scoped PASS, full profile NOT PASS

Repository: `trung-via/ai-commerce`.
Source HEAD: `5190a01301186c8fc9361c1aa21b55caafd63dc4`.
Dependency: exact A12-01 candidate
`efd4d69a177493372fcd8f48e82c901121a49684`.

This is the new authorized A12-02 attempt on the repaired checkout. Initial
`git status --porcelain=v1` was empty. Read-only `git log` proves that the source
HEAD's sole parent is the exact A12-01 candidate. The parent-to-HEAD diff contains
only `.gitattributes`, adding the raw repair-evidence exception. A12-01 was not
reimplemented, and no previous #106/#111 claim/result was altered. This report
describes this executor's observed worktree and runs, not prior worker results.

## Implementation and preservation

* `history.py`: source-namespaced identity read models with immutable snapshot
  retention, idempotent exact replay, explicit correction lineage and retained
  listing/variant conflicts. Product/listing/seller/variant IDs retain the exact
  A12-01 algorithm. No GTIN/SKU cross-merchant merge or canonical winner.
* `claims.py`: scalar value/unit/category, assertion origin, evidence role and
  required status separation. Seller/API assertions stay assertions. Explicit
  reviewer proof requires separate bytes, hash/size and exact claim scope;
  synthetic/unknown/illustrative claims cannot acquire independent status.
  Source authenticity is not externally attested. Counterevidence and source
  conflict references remain visible.
* `offers.py`: explicit variant pin, safe timezone-aware observation/expiry,
  strict decimal-string prices and commission, nullable stock, distinct
  missing/null/zero, NOT_ELIGIBILITY_VERIFIED commission, explicit as-of freshness
  and overlapping-offer conflicts. No currency conversion or live-price claim.
* `evidence.py` and `evidence_schemas.py`: bounded local raw JSON/pointer parsing,
  exact snapshot subject pins, immutable restoration and self-contained versioned
  contracts. Recomputed content hashes cannot bypass raw/subject/report checks.
* `core.py`/`schemas.py`: additive dispatch and optional context arguments for
  the two new contracts. All existing call signatures remain usable. Runtime
  schemas match the two new top-level JSON mirrors and need no A02 registration.
* `README.md`: APIs, local input formats, bounds, epistemic limits and the
  provider-neutral seam. Registry, media rights, CLI and evidence packs remain
  in later actions' scope.

Implementation/schema/test edits are limited to the authorized product path,
the two new schemas and the three named tests. No top-level `__init__` export
changed. The identity builder, snapshot reader, safe-file layer, three original
JSON schemas, `tests/test_a12_snapshot.py` and historical `VERIFICATION.md` have
empty tracked diffs. No A02/A03/A07/A08/A09/A10/A11 API or contract changed.
No external fetch, paid provider, model, account or publication was invoked.

## Observed tests and bounded review

Observed runtime: Windows, CPython **3.14.7**. Python 3.11 syntax was checked
with `ast.parse(feature_version=(3, 11))`; an actual Python 3.11 or Ubuntu run
was not performed. Only this executor was used, with no subagents.

| Stage | Command | Exit | Observed result |
| --- | --- | --- | --- |
| Initial A12 fan-in | `python -m pytest tests/test_a12_identity.py tests/test_a12_claims.py tests/test_a12_offer.py tests/test_a12_snapshot.py -q` | 0 | **283 passed**, 15.69 s |
| After one local review improvement | same command | 0 | **287 passed**, 16.18 s |
| Registered project-tests | `python -m pytest tests -q -n 12` | 1 | **30 failed, 2117 passed, 15 skipped**, 229.42 s |

There were no failing A12 tests in the initial run. One same-executor review
improvement removed repeated snapshot parsing for each identity-history row,
made output-limit rejection atomic and enforced exactly three currency letters
(including rejection of a trailing newline). Four added regressions cover those
cases. Deterministic literal golden IDs pin identity revision, claim and offer
outputs. The final targeted run includes the **136 unchanged A12-01 tests** and
**151 A12-02 cases**.

The new tests cover cross-source/merchant/variant isolation, missing identity
keys, replay metadata mismatch, correction parent/source/subject validation,
raw BOM/CRLF preservation, detached immutable values, all claim statuses,
negative/illustrative/unknown/conflicting evidence, unsupported seller/API
review, separate report and review scope hashes, rehashed forgery, exact decimal
limits, null/missing/zero/false, stock/commission conflict, expiry/age/UTC boundary
cases, future observation, unsafe timestamps, bounded input and JSON pointers.
An offline fan-in case uses A12-01 correction snapshots, a claim and offers
together and verifies freeform Creative `product_ref=None`.

## Evidence and acceptance boundary

Raw stdout/stderr bytes are retained locally in
`a12_02_evidence/{initial.targeted,review1.targeted,project-tests}.{stdout,stderr}.log`.
The existing `.log` ignore rule applies. Adjacent JSON records retain exact
commands, exits, runtime, durations and raw stream SHA-256 values. The final
targeted and registered records also pin implementation/schema/test hashes.
`a12_02_evidence/static.json` records syntax, whitespace, raw status, scope,
preserved dependency hashes, exact parent and sole `.gitattributes` base delta.
Historical A12-01 `repair_evidence` and its raw CRLF blobs were not changed.

AC02–AC04 are satisfied within the observed offline A12 test scope. This is
local code/evidence verification, not authentication of seller facts, reports,
affiliate permissions, media commercial rights, current prices or a published
state. The full registered profile is **NOT PASS**. Candidate publication belongs
only to the EF02 worker; the executor issued no Git write command.

## Registered-profile failures and handoff

The full run has **no A12 failures**. Its 30 failures are confined to
`test_ef01.py` (13), `test_ef01_ef02_integrity.py` (3),
`test_ef01_git_write_guard.py` (6), `test_ef01_performance.py` (1), and
`test_ef01_repair.py` (7). Inspection of all 30 failure blocks finds:

* 29 fixture failures return Git exit 128 with a reference-transaction hook
  rejection; some include MSYS `NtCreateDirectoryObject` / `0xC0000022`.
* One performance-test assertion requires `GIT_CONFIG_COUNT` to be absent from
  the child environment. It is inherited in this sandbox.

Fingerprint: `project-tests | Windows/CPython 3.14.7 | fixture Git init 128 |
reference-transaction hook / 0xC0000022 | source 5190a013...`; separately,
`test_ef01_performance | inherited GIT_CONFIG_COUNT assertion`.
These are observed environmental/test-fixture failures outside the A12 action's
allowed paths. No pristine baseline comparison was run, so this report does not
claim all failures are pre-existing code defects. No hooks, environment, Git
options, infrastructure or outside-scope files were changed, and the failed
profile was not replayed with alternate settings. The 15 skips cite unavailable
native hardlink/symlink/junction permissions. No actual Python 3.11/Ubuntu or
hosted-worker acceptance is claimed.

`a12_02_evidence/project-tests.failures.json` retains all 30 failure IDs,
per-file counts and per-block signature evidence. Full-profile source hashes
match before/after; HEAD remains the exact original source SHA. The final
scope/source review is in `a12_02_evidence/final-source.json`.

The scoped worktree is ready for EF01 to inspect and verify in the authorized
worker environment. It is **not a verified/published candidate** and is not
merged to main. There is no new unknown worktree contamination; only explicitly
authored allowed paths are changed.
