# BQ-04b R13 source handoff

Action `BQ04B-R13-AGY38HIGH-TRUSTED-ORACLE-POST616-01`; attempt `bq04b_r13_agy38high_trusted_oracle_post616_20261107_v1`; source `2a4561e8ff7c855d430c298ee8f2c21921f80797`; requested executor `antigravity/gemini-3.8-flash/high`, no silent fallback. Initial executor checks confirmed exact source root, clean worktree at main `2a4561e8ff7c855d430c298ee8f2c21921f80797`, seven absent ADD paths and six existing MODIFY paths. EF02 alone owns candidate publication.

## Historical evidence, provenance, and Root Fixes

1. **#604 R11 Historical Candidate**: Commit `8a1285bd0e91c63e6590cdabb1ece45ba21807e7` achieved 1281 Windows MT01 PASS, but was placed on [Brain HOLD at issuecomment-6704905880](https://github.com/trung-via/ai-commerce/issues/604#issuecomment-6704905880). The root flaw was that caller-provided dictionary context `synthetic_fixture_profile=bq04b-white-slate-4fps/v1` combined with black frames at 1/2 and 3/4 promoted `SYNTHETIC_WHITE_SLATE_BLACK_SIGNAL -> PROPOSE_LOCAL_DETERMINISTIC_EDIT`. This was unsafe because genuine creative footage can legitimately contain black frames.
2. **#611 R12 Attempt**: Run 42359144994, artifact 13569642458. The provider exited 0, but the AGY read-only input gained a `.pytest_cache` directory, causing `AGY_INPUT_INTEGRITY_FAILED`. Its nine-file quarantine is read-only advisory; replay was unauthorized.
3. **#616 EF01 Infrastructure Rootfix**: PR #620 was merged to main at `2a4561e8ff7c855d430c298ee8f2c21921f80797`. Runner updated `_agy_child_env` with `PYTEST_ADDOPTS=-p no:cacheprovider`, bytecode disabled, and temporary/cache directories cleanly placed outside `READ_ONLY_INPUT`.
4. **NEW R13 Fresh Attempt**: Authored freshly on clean main `2a4561e8ff7c855d430c298ee8f2c21921f80797`. Does not replay or cherry-pick quarantined artifacts.

## Root Semantic Negative AC Resolution

Generic BQ04a `decide_repair` NEVER treats an untrusted caller context dictionary, strings, forged oracle, receipt, asserted rights/claim or guessed hashes as an observed defect. Even with legitimate black Q4 measurements, false positive MUST fail (`KEEP_ORIGINAL` or `HOLD`, no local edit) unless there is a native grounded fault independent of caller profile.

The solution implements a test-owned, live native fixture capability verifier:
- Tied to fixed media SHA (`state["original"] == asset_sha256`).
- Tied to BEFORE observed Q4 (`state["before"].digest == review.digest`).
- Tied to exact time/geometry: 64x64, 4fps, 2.0s duration (8 frames), target frames 2 and 3 (times 1/2 and 3/4) observed full-frame black (luma 0).
- Tied to active fixture session lifetime: once the `synthetic_fixture` context manager exits, `_released=True` and the session is deregistered from `_SESSIONS`.
- Regression verification:
  1. Same genuine black frames with forged synthetic caller context yields `KEEP_ORIGINAL` (NO proposed edit).
  2. Forged fixture token/JSON, stale SHA, or released fixture yields `HOLD` fail-closed.
  3. Real disposable fixture during active lifetime proposes bounded correction (`PROPOSE_LOCAL_DETERMINISTIC_EDIT`).

## Native Windows Link Contract

The fail-closed gate `_reject_metadata` rejects symlink mode, reparse points (`FILE_ATTRIBUTE_REPARSE_POINT 0x400`), any nonzero reparse tag (e.g. junction tag `0xA0000003`, symlink tag `0xA000000C`), and file link multiplicity before media is opened.
- Real native hardlinks (`os.link`) and junctions (`mklink /J`) are constructed and verified in Windows testing.
- Hardlink multiplicity (`nlink == 2`) rejects with `LINK_MULTIPLICITY`.
- Directory junction (`reparse tag 0xA0000003`) rejects with `LINK_OR_REPARSE`.
- Tripwires on `_hash`, `_tools`, `_runner`, `_frames`, `open_regular`, and `_snapshot_lock` prove rejection occurs before any media reads.
- Metadata injection tests pass all reparse tag variations through the actual verifier.
- Ordinary-metadata positive control confirms that legitimate files undergo actual reinspection.
- `SYMLINK_NATIVE_EFFECT_UNPROVEN`: Injected metadata and native junctions do not claim OS symlink creation without privilege. Mandatory failures are typed `NATIVE_LINK_ENVIRONMENT_HOLD` (never skipped or xfailed).

## Verification Scope

Registered `mt01-component-onboarding` with exact positive ACs:
- `BQ-04b.q5-deterministic-edit-fixture-isolated`
- `BQ-04b.q5-artifact-identity-attempt-receipt`
- `BQ-04b.q5-independent-technical-reinspection-fail-closed`

Selected groups must be exactly `a13`, `bq-02a`, `bq-02b`, `bq-03b`, `bq-04a`, and `bq-04b` with zero fallback or missing mappings.

### Observed Local Test Results

- `test_bq_04b_offline_repair.py`: 105 passed, 0 failed, 0 skipped.
- `test_bq_04a_targeted_repair.py`: 153 passed, 0 failed, 0 skipped.
- `test_bq_03b_human_study_readiness.py`: 298 passed, 0 failed, 0 skipped.
- `test_bq_02b_multimodal.py`: 249 passed, 0 failed, 0 skipped.
- `test_bq_02a_critic_t1.py`: 282 passed, 0 failed, 0 skipped.
- `test_a13_*.py` suite: 197 passed, 0 failed, 0 skipped.
- Minimum sufficient owner+consumer total: 1284 passed, 0 failed, 0 skipped.
- All temporary output, pytest cache, basetemp, and FFmpeg scratch directed to writable scratch; read-only input manifest intact.
