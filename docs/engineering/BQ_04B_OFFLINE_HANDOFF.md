# BQ-04b R14 source handoff

Action `BQ04B-R14-AGY38HIGH-REMOVE-CALLER-BEARER-AUTH-01`; attempt `bq04b_r14_agy38_high_no_bearer_token_main_2a4561_20261107_v1`; source `2a4561e8ff7c855d430c298ee8f2c21921f80797`; requested executor `antigravity/gemini-3.8-flash/high`, no silent fallback. Initial executor checks confirmed exact source root, clean worktree at main `2a4561e8ff7c855d430c298ee8f2c21921f80797`, seven absent ADD paths and six existing MODIFY paths. EF02 alone owns candidate publication. Status marked as `AWAIT_EF02_NATIVE_VERIFICATION`.

## Historical evidence, provenance, and Root Fixes

1. **#604 R11 Historical Candidate**: Candidate SHA `8a1285bd0e91c63e6590cdabb1ece45ba21807e7` (artifact ID `13568267969`, artifact zip SHA256 `97af73bd572d6d0e1baf49e9e5e88f54337e7c1b0b7ecd12e4a298ee8533cfd7`) achieved 1281 Windows MT01 PASS, but was placed on [Brain HOLD at issuecomment-6704905880](https://github.com/trung-via/ai-commerce/issues/604#issuecomment-6704905880). The root flaw was that caller-provided dictionary context `synthetic_fixture_profile=bq04b-white-slate-4fps/v1` combined with black frames at 1/2 and 3/4 promoted `SYNTHETIC_WHITE_SLATE_BLACK_SIGNAL -> PROPOSE_LOCAL_DETERMINISTIC_EDIT`. This was unsafe because genuine creative footage can legitimately contain black frames.
2. **#611 R12 Attempt**: Run 42359144994, artifact 13569642458. The provider exited 0, but the AGY read-only input gained a `.pytest_cache` directory, causing `AGY_INPUT_INTEGRITY_FAILED`. Its nine-file quarantine is read-only advisory; remained input integrity quarantined with no candidate.
3. **#616 EF01 Infrastructure Rootfix**: PR #620 was merged to main at `2a4561e8ff7c855d430c298ee8f2c21921f80797`. Runner updated `_agy_child_env` with `PYTEST_ADDOPTS=-p no:cacheprovider`, bytecode disabled, and temporary/cache directories cleanly placed outside `READ_ONLY_INPUT`.
4. **#623 R13 Attempt**: Run 42367163217, artifact 13572867915 (GitHub SHA256 `c280671d05c04029ce0af6f44d4663b8b1e2f66e1d74deeacc8b82f02db0860c`), historical candidate `30d25f2e3ff4c0e59575b53fde76d575a50165bb` on `ef02/candidate/d4d16332ab9167985650889cf449d6b5`. True EF02 attempt was `bq04b_r13_agy38_high_oracle_post616_2a4561_20261107_v1`, whereas executor-local WorkItem claimed `bq04b_r13_agy38high_trusted_oracle_post616_20261107_v1` (stale executor-local lineage). R13 was placed on [Brain HOLD at issuecomment-6706677521](https://github.com/trung-via/ai-commerce/issues/623#issuecomment-6706677521) because `repair_policy` accepted caller bearer strings from context (`ctx.get('fixture_token')`) and `repair_offline_adapter.verify_live_fixture_capability` looked up active tokens in `_SESSIONS`, allowing a copied token alone to authorize a proposal without `fixture=` being passed.
5. **NEW R14 Fresh Attempt**: Action `BQ04B-R14-AGY38HIGH-REMOVE-CALLER-BEARER-AUTH-01`, attempt `bq04b_r14_agy38_high_no_bearer_token_main_2a4561_20261107_v1` on main `2a4561e8ff7c855d430c298ee8f2c21921f80797`. Completely removes caller bearer token strings as authorizers and marks R13 differences as stale lineage.

## Root Semantic Negative AC Resolution

Generic BQ04a `decide_repair` NEVER treats an untrusted caller context dictionary, strings, bearer tokens, forged oracle, receipt, asserted rights/claim or guessed hashes as an observed defect:
- **Proposal Authority**: Only an exact, live, unforgeable native `FixtureSession` object passed via explicit `fixture=` can authorize synthetic technical fault classification (`SYNTHETIC_WHITE_SLATE_BLACK_SIGNAL -> PROPOSE_LOCAL_DETERMINISTIC_EDIT`).
- **No Token-Only Authorization**: Adapter verifier `verify_live_fixture_capability` drops token-only lookup; caller bearer token strings alone in context (`fixture_token` or `synthetic_fixture_token`) fail closed to `HOLD`.
- **Contradictory Token Gate**: If `fixture=` is provided but context includes a contradictory or malformed token, the request fails closed to `HOLD` instead of accepting either authority.
- **Fixture Lifecycle & Binding**:
  - Bound to fixed media SHA (`state["original"] == asset_sha256`).
  - Bound to BEFORE observed Q4 (`state["before"].digest == review.digest`).
  - Bound to exact time/geometry: 64x64, 4fps, 2.0s duration (8 frames), target frames 2 and 3 full-frame black.
  - Active lifetime enforced: once the context manager exits, `_released=True` and session is evicted from `_SESSIONS`.
- **Regression Verification**:
  1. Minted active bearer token copied to context without `fixture=` yields `HOLD` with null `grounded_fault`.
  2. Minted active bearer token in `synthetic_fixture_token` without `fixture=` yields `HOLD` with null `grounded_fault`.
  3. Dict carrying both synthetic profile and copied token without `fixture=` yields `HOLD` with null `grounded_fault`.
  4. Valid token from one fixture tested against another fixture yields `HOLD` fail-closed.
  5. Positive `fixture_parents(fixture, before)` passes true `fixture=`, yields bounded synthetic-only edit.
  6. Direct `verify_live_fixture_capability(token=fixture.token, ...)` yields `(False, "BEARER_TOKEN_CANNOT_AUTHORIZE")`.

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

- `test_bq_04b_offline_repair.py`: 106 passed, 0 failed, 0 skipped.
- `test_bq_04a_targeted_repair.py`: 154 passed, 0 failed, 0 skipped.
- `test_bq_03b_human_study_readiness.py`: 298 passed, 0 failed, 0 skipped.
- `test_bq_02b_multimodal.py`: 249 passed, 0 failed, 0 skipped.
- `test_bq_02a_critic_t1.py`: 282 passed, 0 failed, 0 skipped.
- `test_a13_*.py` suite: 197 passed, 0 failed, 0 skipped.
- Minimum sufficient owner+consumer total: 1286 passed, 0 failed, 0 skipped.
- All temporary output, pytest cache, basetemp, and FFmpeg scratch directed to writable scratch; read-only input manifest intact.
