# A14 L04 implementation handoff

Source: `54822f494b3119fc2edaab357b63333aa4fcec48` (unchanged HEAD).
Action: `a14_l04`, attempt `a14_l04_20261102_main54822f_v1`.
Authorization: the local immutable EF02 request for Issue #170, batch
`a14-l04-main54822f-20261102-v1`; no external issue/network lookup was performed.

## Delivered scope

Only the four dispatched path grants are used:

- `src/ai_commerce/affiliate_attribution`
- `contracts/AffiliateAttributionEvidence.v1.schema.json`
- `tests/test_a14_attribution.py`
- this report

`AffiliateAttributionEvidence.v1` supplies strict, bounded, offline JSON replay,
an immutable Python evidence object, a mirrored self-contained Draft 2020-12
schema, append-only serializable history, and a standalone stdin/stdout module
CLI. Existing root CLI, consumers, engineering registry, verification profiles,
Git metadata, configuration and protected files are unchanged.

Tier A requires an unambiguous supplied order→click→link→creative chain with
consistent timestamps. B retains a provider-reported creative association. C
retains cohort scope. D retains directional or incomplete evidence. The declared
algorithm is independently recorded; this task does not implement touch-model
allocation. No tier establishes actual orders, causality or verified per-video
profit. Missing joins do not create identifiers or infer purchases.

Provider, platform, account namespace/ID, app, market, surface, explicit UTC
measurement window, cohort, link, creative hash and supporting event references
retain provenance. The exact input UTF-8 bytes, raw and canonical SHA-256 values,
scoped event digests and evidence ID replay deterministically. Expected source
hash/evidence ID pins detect substitution. Checksums are not signatures or
external proof of source authority. Duplicate event rows deduplicate; ambiguous
stable identifiers fail with explicit collision codes.

`AVAILABLE=0`, `UNKNOWN` and `NOT_AVAILABLE` remain distinct. Reported order
states `PENDING`, `REFUNDED`, `REVOKED` and `SETTLED` remain distinct from each
other and from link revocation. No amounts, refunds arithmetic, contribution,
settlement receipts, revenue, payouts or actual entitlements are generated.
Even supplied `SETTLED` remains unverified; actual commercial fields stay UNKNOWN.

History accepts exact retries as no-ops. New bytes under an existing key fail
`IDEMPOTENCY_KEY_COLLISION`. Changed identifier bindings fail explicit collision
codes across overlapping windows in the same provider/account scope. Corrections
require a complete replacement source snapshot, the actual parent, unchanged
scope/source kind/model, a new key and changed normalized events. Earlier bytes
remain retained. Competing distinct children fail `CONFLICTING_CORRECTION` before
mutation. History replay checks every record, parent and active binding. History
does not sum snapshots, and persistence/concurrent access remain caller-owned.

Inputs accept only a narrow caller-prepared projection with nonpersonal business
aliases, not arbitrary provider exports. Unknown/free-text/tracking/contact/
credential/payment fields, URLs and network addresses are rejected by the typed
projection. The caller must ensure opaque aliases do not encode personal data.
The component has no filesystem/network/provider/payment/CRM/browser operations.
Its CLI reads/writes bounded standard streams only.

## Consumer contracts read

- A06 `E01Manifest.v1` and `E01Delivery.v1`: rendered-output provenance and hashes
  do not establish publication, link activation or orders.
- A07 `ChannelCapabilityRequest/Decision/Snapshot.v1` and evaluator: recorded
  offline eligibility does not establish live provider authority.
- A08 `DistributionPackage.v1`, publication contract and matching consumer:
  manual handoff, publication and independent public visibility stay separate.
- A09 `PerformanceObservation.v1`, model and receipt matching: explicit account,
  metric definition, source and window; unavailable data is not zero; a receipt
  match alone is not commercial attribution.
- A12 `ProductEvidencePack.v1` and public exports: product/variant/offer/rights
  evidence does not grant affiliate entitlement.
- A13 placement/release schemas, public APIs and documentation: offline advisory
  review does not assert active links, approval, orders or earnings.

Upstream references are preserved as explicit component/reference/raw-hash pins,
with `HASH_PINS_ONLY_NOT_OPENED`. They are not imported as verified upstream
objects or used to upgrade a tier. This is L04 only: no E04 orchestration,
native-provider adapter, publication, cookies/IP/device fingerprinting, actual
affiliate entitlement, purchase creation or A16 settlement implementation.

## Observed verification

- Focused L04: **120 passed**, exit 0, 1.26 seconds.
  Command: `python -B -m pytest tests/test_a14_attribution.py -q -p no:cacheprovider`.
- L04 plus all existing A06/A07/A08/A09/A12/A13 consumer test files:
  **1031 passed, 5 skipped**, exit 0, 46.56 seconds pytest time.
  Twelve xdist workers; exact command, interpreter, elapsed time, tested file
  SHA-256 values and output digest are retained in
  `src/ai_commerce/affiliate_attribution/verification/verification.json`.
  Exact subprocess output bytes are retained in `verification/consumer-tests.txt`.
- All eight tested implementation/schema/test/documentation files remained
  byte-identical throughout the consumer run. No implementation changed after it.
- Replay compares canonical JSON bytes rather than Python value equality;
  integral floats cannot alias hashed integers in retained source documents or
  derived results. The final focused and consumer runs include this regression
  and supersede the earlier 119/1030-pass runs.
- Python 3.11 syntax parsed for all five package Python modules and the test file.
  Runtime verification used the runner's actual interpreter recorded in the proof.
- Runtime and checked-in schema mirrors match; schemas have no remote `$ref`.
- Allowed-path coverage, ordinary-file/protected-path checks, unchanged pinned
  HEAD and absence of existing tracked-file changes passed at final handoff.

The five native-filesystem skips are two A09 symlink tests, A12 hardlink and
symlink tests, and an A13 media link test. This sandbox denies creating those
links; native behavior remains unproven here. Existing deterministic rejection
checks ran. No live provider, publication, sale or settlement was tested or claimed.

The registered `project-tests` profile remains **PENDING_PARENT_EF01**. Its
engineering fixtures invoke Git writes in disposable repositories; the executor
was expressly instructed not to issue Git writes or bypass the inherited guard.
The parent EF01/EF02 environment must perform registered full verification and
independently snapshot candidate bytes. The profile/registry was not altered to
make a selective run appear authoritative. No commit, ref, branch or publication
was created by this executor.

## Candidate byte handoff

`src/ai_commerce/affiliate_attribution/verification/candidate-bytes.json` records
each final candidate path, byte length and SHA-256, excluding only that manifest's
own bytes to avoid self-reference. Its own length/hash is reported in the final
executor handoff. It includes this report and the exact verification output/proof.
This is a local byte inventory, not an EF02 publication receipt or full-suite PASS.
