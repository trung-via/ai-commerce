# BQ-06b — CI-first fresh evidence protocol (Issue #730)

**Source branch:** `bq06b/ci-first-reauthor-730-20261109` from immutable baseline `1dd406272f50d95d581c9e4b3a759f455db7cd22`.

**State:** implementation candidate **UNVERIFIED** pending native Windows test, MT-01 owner+consumer selection, GitHub Actions exact-head hosted CI and independent Brain review. This is not production/live analytics, executor return, trusted merge or provider effect. #698 stays `HOLD_UNKNOWN_EFFECT`.

## Bounded owner read

Caller supplies independently retained, exact A09 `SnapshotPin`, A11 `expected_export_id`, learning key/revision/context/metric and genuine BQ06a output bytes. No implicit discovery or repinning. Run `review_fresh(a09_path=..., snapshot_pin=..., bq06a_raw=..., source_arguments=...)`. The output is `BQ06bFreshEvidenceReview.v1`; pass to `recheck_fresh` with original independent arguments to replay native sources. The output hash is an identity, not a signature or independent assertion of trust.

The native read:
1. Reject non-Windows and unsafe links, reparse, hardlink, ADS/relative paths, SQLite sidecars or nonquiescent lock states using existing BQ06a/A09/A11 owner guards. Freeze both A09 and A11 bytes together, plus A17 read-only source tree if supplied, and hold the native locks until the final byte comparison.
2. Recheck BQ06a native A11/A17 review, not a caller-authored JSON status. A09 `read_snapshot` independently replays exact import/raw/mapping/observations. Both DBs are independently opened read-only; source identity locks reduce races but do **not** turn two DBs into one transaction. A missing guarantee must HOLD.
3. Enumerate the *entire* same-scope native A09 `AnalyticsLedger.history` using pages of at most 100, overall bounded to 512. Check monotonic cursor, uniqueness, exact scope and native dict `lineage: {relationship, parent_import_id}`; `CORRECTION` / `REPROCESS` direct and transitive descendants invalidate an older selected import. No implicit switch to the latest child. Incomplete history => `HISTORY_UNPROVEN`.
4. Compare all selected A11 observation references against genuine full A09 `ObservationSnapshot.observation_ref` dictionaries, never a partial creative/name/cohort match. BQ06a archived review must independently pass its exact A11/A17 pins.
5. Return only finite codes and bounded hash/count fields. `EVIDENCE_REVIEW_FOR_BRAIN` means **local** replay only, with **no** provider attestation, controlled experiment, ROI, causal lift, human approval, automatic A17 adoption, PULSE publish or cost permission.

## Historical regression anchors

- **#694**: original R1 Brain HOLD because A09 `lineage` is **dict**, not list; it did not examine descendants. Candidate `b52de195f152f56fc2a549d958ab84e73d4203ec` remains unmerged, never reused.
- **#698**: R2 last WorkOwner state `PROVIDER_INTENT`, no authentic provider return. Do **not** rerun or transfer.
- **#723/#726**: source-only HostOps R0.2 merged and native read returned `BATCH_ABSENT`, `SOURCE_READ_UNKNOWN`; these are not no-effect attestations and do not block independently authorized CI-first source authorship.

## Test/acceptance policy

Primary AC IDs: `BQ-06b.a09-raw-replay-pinned-to-a11`, `BQ-06b.missingness-and-comparability`, `BQ-06b.brain-review-non-authoritative`.

Required targeted negative matrix: authentic append-only correction, reprocess and two-hop descendants; unrelated roots; pagination over 100 and incomplete cursor; invalid or stale pin/selected A11 revision/ref/metric/window/cohort/timezone/denominator; duplicate observations and zero vs missing; native hardlinks, junctions, symlink and ADS; A09/A11 race and WAL; input forgery; source byte immutability. A native symlink creation privilege failure is explicitly *UNPROVEN SKIP*, never a passed negative. Existing A09/A11/A17/BQ06a tests and all declared downstream MT-01 consumers must remain valid.

**Efficiency:** exact negative reproducer → only registered minimum owner+consumer test closure → hosted Windows exact-head CI; maximum two diagnostic/repair rounds for the same new failure family. No repetitive unlimited EF02 actions or full suite without verified impact. No CI result may be invented before an actual GitHub Actions run and JUnit artifact have been checked.

## Result vocabulary

- `EVIDENCE_REVIEW_FOR_BRAIN`: selected local source and advisory were independently replayed under pinned source locks; no external effect.
- `INSUFFICIENT_EVIDENCE_HOLD`: one or more finite `missing_requirements`.
- `A09_LINEAGE_STALE_OR_CORRECTED`: selected import has a later legitimate native lineage descendant.
- `HISTORY_UNPROVEN`: native complete bounded history could not be trusted.
- `CROSS_OWNER_ATOMICITY_UNPROVEN`: native source/cross-db/locking proof missing.
- `A11_OBSERVATION_REFS_MISMATCH`: full original A11 evidence reference not present in exact A09 snapshot.
- `A11_ARCHIVED_REVIEW_HOLD`: independent retained A11/A17 review has unmet mandatory prerequisites.

Only an independently reviewed candidate with authentic Windows results may receive Brain PASS and trusted merge. This protocol does not change #698 WorkOwner, HostOps, protected workflow, external provider policy or Human-grant boundaries.
