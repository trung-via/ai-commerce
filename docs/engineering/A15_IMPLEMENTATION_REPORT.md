# A15 Operations — Engineering implementation report

## Status
**INITIAL REVIEW CANDIDATE / NOT ACCEPTED / NOT MERGED**. The actual result and test counts must be read from GitHub Actions at the exact candidate HEAD; there is no claim of local or hosted PASS in this source document.

- Handoff: https://github.com/trung-via/ai-commerce/issues/237
- Parent main observed when authoring: `41cb7835090bd96cf03648ea12606766e715e8ae`
- Roadmap: Phase 4 A15, milestone M5; workstreams O05, O06, O07.
- Existing dependency: A04 implemented O01–O04 with `ArtifactRegistry`, `AssetJobManifest`, `LocalMediaRunner`, `GenerationJobRunner`; preserve all contracts and their existing V1/V2 receipts.

## Source delivered for review
`src/ai_commerce/operations/cost_ledger.py` — immutable checksum-sealed explicit offline cost evidence, decimal-string currency, strict status and supplied-evidence source classification, synthetic vs supplied charges reported separately. External provider cost is never authenticated by this component.

`src/ai_commerce/operations/retry_guard.py` — no-provider-call immutable intent and attempt observation, consecutive attempt inventory, identical replay, conflict on changed bytes, fail-closed on unknown effects and stale last-attempt references. Even when a safe local failure is observed, the result is `REQUIRES_HUMAN_AUTHORIZATION`, not an executed retry.

`src/ai_commerce/operations/status_report.py` — read-only projection of validated A04 media manifest, selected cost evidence and optional guarded retry decision. Publication, link, click, order, settlement remain separate and cannot be promoted to confirmed business events. No CLI or API network integration is activated by this first patch.

`tests/test_a15_operations.py` — positive and negative fixtures including replay, byte collision, corrupted hash, bad decimal, UNKNOWN/PENDING missingness, mixed currency, synthetic charge, no-blind-retry, stale attempt, invalid proof, A04 dry-run consumer and wrong-job-cost isolation.

## Scope and truth limits
- Offline-only; no network, paid model invocation, production account access, payment, media publication, or actual settlement.
- `CHARGED` is a **supplied evidence claim**, not independently provider-confirmed billing. Synthetic fixture charges are excluded from supplied totals. No `REVENUE` field is inferred.
- `ESTIMATED`, `PENDING`, `UNKNOWN` and `NOT_APPLICABLE` are distinct. Real provider receipt reconciliation, currency conversions and A16 contribution review are deferred.
- The existing A04 `Cost` only has UNKNOWN, NOT_APPLICABLE, ESTIMATED, CHARGED. This new evidence model does not alter it.
- A05/A06 rendering can continue without costs, provider accounts or affiliate link entitlement.

## Required verification
`python -m pytest tests/test_a15_operations.py tests/test_a04_media_operations.py tests/test_a04_upgrade_v11.py tests/test_minimum_test.py tests/test_ef02_mt01_registry.py -q -n 4 --dist loadfile` with the hosted required FFmpeg/Pillow setup where selected. `mt01-a15-tests` profile is separately registered with `-n 12` for authorized EF02. CI on both OS at the final PR HEAD required. Do not infer external operation from mock provider receipts.

## Remaining A15 acceptance before Brain PASS
- Validate stable interface/consumer tests, exact-head CI, mapping and oracle-selected coverage.
- Explicit read-only operational status CLI is not yet delivered; may be added to the *same review PR* after examining CLI compatibility, not via a new symptom PR.
- Full A15 M5 product acceptance requires the read-only operator workflow demonstrated with trusted source/failure and required regressions; M5 also includes A16, so A15 merge alone cannot imply M5.
