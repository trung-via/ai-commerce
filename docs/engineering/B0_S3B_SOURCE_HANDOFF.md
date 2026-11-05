# B0-S.3b Offline Broker Selection and Isolation Readiness Source Handoff

Status: **S3B_SELECTION_AND_ATTESTATION_PLAN_ONLY / HUMAN_PLATFORM_AND_AUTHENTICATOR_SELECTION_PENDING**.
Live custody state: **`PRIVATE_CUSTODY_VERIFIED=UNKNOWN` / `LIVE_VERIFIED=IMPOSSIBLE_IN_THIS_CUT`**.
Post-executor EF01 status: **`PENDING_NOT_RUN_BY_EXECUTOR`** (verified candidate publication belongs exclusively to the outer EF02 worker after authentic post-executor EF01 scope proof).

---

## 1. Source Pin, Executor Pin, and Preflight Scope

- **Exact baseline and unchanged `HEAD`:** `c7ae34c5a670277aaadbd422404fd41a06959d11` (`main`; merged B0-S.3a PR #374, B0-S.1/S.2 PR #365, research [Issue #356](https://github.com/trung-via/ai-commerce/issues/356), [PA2 Roadmap B0-S.3 G05/G08](../roadmap/ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md)).
- **Human explicit authorization (2026-11-05):** `"ok tiếp tục, sử dụng antigravity gemini 4 argon medium"` (`HUMAN_EXPLICIT` override).
- **Executor / Model / Effort Pin:** `antigravity` / `gemini-4-argon` (Gemini 4 Argon) / `medium`.
- **Action ID:** `B0-S3B-BROKER-SELECTION-READINESS-01`.
- **Preflight & Final Scope:** Read-only `git rev-parse HEAD`, `git status --short`, and `git diff` verified a clean checkout at `c7ae34c5a670277aaadbd422404fd41a06959d11` with all eight allowed target paths absent before edits, zero tracked file modifications, and an unchanged 806-file protected worktree fingerprint (`9317ab913fccb9fe1c11767c62f033bfab1c51b92d564f40d0f260012ce31ad3`) before and after verification.
- **Native B0.3 Source Lock:** Remains `32f42b84567e1217a93854389b1c96435bbf454d` in unchanged `security_policy.py` and `attended_worker.py`.
- **Exclusions Enforced:** Zero edits to `security_claims.py`, `security_policy.py`, `custody_claims.py`, `custody_gate.py`, any B0.1–B0.4 source/test, schemas, HostOps, EF01/EF02, `.github`, or model defaults. No git commits/branches/pushes, PRs, merges, cloud accounts, secrets, or network/provider calls were performed.

### Eight Add-Only Authorized Paths

1. [`B0_S3B_BROKER_SELECTION_MATRIX_V1.md`](../security/B0_S3B_BROKER_SELECTION_MATRIX_V1.md) — Vendor-neutral primary-sourced comparison across off-host patterns (`P1` Edge Worker + Durable Object, `P2` Azure Managed Compute + Key Vault, `P3` Google Cloud Run + Secret Manager, `P4` Separate Device Broker, plus rejected same-SID/KMS-only/Passkey-only baselines), selection gates `G01`–`G10`, and credential-free positive/negative trial tasks.
2. [`B0_S3B_ISOLATION_ATTESTATION_PLAN_V1.md`](../security/B0_S3B_ISOLATION_ATTESTATION_PLAN_V1.md) — Stepwise future read-only installation checklist, threat table `T01`–`T20`, independent no-secret redacted-canary proof, out-of-band auditor requirements, and strict separation of `DESIGNED`, `SIMULATED_ONLY`, `UNPROVEN`, and `LIVE_VERIFIED`.
3. [`broker_intake.py`](../../src/ai_commerce/production_runtime/broker_intake.py) — Pure standard-library, closed, bounded (`<=16384` bytes, depth `<=8`, `<=512` nodes, `<=64` entries, `<=1024` chars) canonical parser for `BrokerSelectionDraft.v1`, `BrokerIsolationClaimDraft.v1`, and `BrokerReceiptDraft.v1`, normalizing every input as `UNTRUSTED` (`verified=False`, `verification_status="UNPROVEN"`).
4. [`broker_readiness.py`](../../src/ai_commerce/production_runtime/broker_readiness.py) — Deterministic fail-closed readiness and composed B0-S.2/B0-S.3a/B0-S.3b gate evaluator (`preview_selection`, `evaluate_readiness`, `decide_external_effect`), capped at `DESIGN_READY_FOR_HUMAN_SELECTION` or `HOLD`/`REJECT`, forcing all simulated `"isolated"`, `"keys_safe"`, `"human_verified"`, and `"provider_ready"` claims to `"UNPROVEN"`, and keeping external effects at `HOLD_PRIVATE_VERIFIER_NOT_ENROLLED`.
5. [`test_b0_s3b_broker_readiness.py`](../../tests/test_b0_s3b_broker_readiness.py) — 260 deterministic parametrized synthetic tests covering all positive inert previews and negative threat classes (`T01`–`T20`).
6. [`b0-s3b.json`](../../work_items/mt01-components/b0-s3b.json) — Data-only `MT01ComponentOwner.v1` manifest declaring `B0-S.3b.broker-selection-readiness` and `B0-S.3b.private-isolation-evidence-gates`, plus existing B0-S.2, B0-S.3a, and B0.1–B0.4 consumers.
7. [`B0-S3B_PRIVATE_BROKER_READINESS_001.json`](../../work_items/B0-S3B_PRIVATE_BROKER_READINESS_001.json) — Complete structured work-item record with Round 1 per-group/per-worker verification receipts, hashes, and historical sibling context.
8. [`B0_S3B_SOURCE_HANDOFF.md`](B0_S3B_SOURCE_HANDOFF.md) — This engineering handoff report.

---

## 2. Observed Local Runtime Environment and Tooling Status

- **Operating System:** Windows 11 (`Windows-11-10.0.26300-SP0`)
- **Python Runtime:** Python `3.14.7`, `pytest 9.2.0`, `pytest-xdist 3.8.0`
- **FFmpeg / FFprobe Status:** Observed available on host PATH (`ffmpeg version 9.0.1-essentials_build-www.gyan.dev`, `ffprobe version 9.0.1-essentials_build-www.gyan.dev`), used by existing `b0-3` and `b0-4` consumer regression suites.
- **Self-Host Runner Identity Caveat:** Local executor pytest runs provide diagnostic verification only (`LOCAL_WINDOWS_EXECUTOR_DIAGNOSTIC_NOT_AUTHENTICATED_SELFHOST_OR_POSTEXECUTOR_EF01`). Authentic post-executor EF01 receipt generation is performed by the outer EF02/EF01 harness after this executor exits.

---

## 3. MT-01 Onboarding Closure and Round 1 Verification Receipts (`-n 12`)

Local `validate_component_onboarding_manifest` admitted `work_items/mt01-components/b0-s3b.json` (`VALIDATED_DATA_ONLY_NOT_VERIFICATION_PASS`, SHA-256 `fbe9b6787ccc5d9afb60ccd16a1cce5640d3e916c21fd1df2cacc09b1578366c`). `select_impact` returned mode `AFFECTED` (`ONBOARD_CONSUMER_OWNER_CLOSURE`, zero `missing_mapping`) and selected the 7 scoped owner and consumer groups below.

Verification completed in **1 round** (`rounds_used=1`, `rounds_remaining=1`, zero post-test source edits required) using isolated session temporary `cache_dir` and `basetemp` directories outside the repository worktree.

| Group | Registered / Dynamic Test File | Selected | Collected | Executed | Passed | Failed | Skipped | Errors | Not Executed | Actual Collecting Workers | Duration (s) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `b0-s3b` | `tests/test_b0_s3b_broker_readiness.py` | 260 | 260 | 260 | 260 | 0 | 0 | 0 | 0 | 12 (`gw0`–`gw11`) | 1.663 |
| `b0-s3` | `tests/test_b0_s3_custody_boundary.py` | 255 | 255 | 255 | 255 | 0 | 0 | 0 | 0 | 12 (`gw0`–`gw11`) | 1.608 |
| `b0-s` | `tests/test_b0_s_trust_boundary.py` | 194 | 194 | 194 | 194 | 0 | 0 | 0 | 0 | 12 (`gw0`–`gw11`) | 1.713 |
| `b0-1` | `tests/test_b0_production_contracts.py` | 82 | 82 | 82 | 82 | 0 | 0 | 0 | 0 | 12 (`gw0`–`gw11`) | 1.586 |
| `b0-2` | `tests/test_b0_2_journal.py` | 60 | 60 | 60 | 60 | 0 | 0 | 0 | 0 | 12 (`gw0`–`gw11`) | 7.382 |
| `b0-4` | `tests/test_b0_4_operator.py` | 69 | 69 | 68 | 68 | 0 | 1 | 0 | 0 | 12 (`gw0`–`gw11`) | 73.301 |
| `b0-3` | `tests/test_b0_3_attended_worker.py` | 55 | 55 | 54 | 54 | 0 | 1 | 0 | 0 | 12 (`gw0`–`gw11`) | 50.610 |
| **Total (7 groups)** | **Scoped B0-S.3b + B0-S1/S2/S3a/B0.1–B0.4 closure** | **975** | **975** | **973** | **973** | **0** | **2** | **0** | **0** | **12 per group** | **137.863** |

### Separate `gw0` through `gw11` Worker Collection Proofs

Every group above recorded 12 distinct `pytest_xdist_node_collection_finished` callbacks (`gw0`..`gw11`) with identical ordered node-ID collection SHA-256 digests (`UTF-8` LF-joined ordered node IDs without trailing LF):

| Group | Ordered Collection SHA-256 (Identical Across `gw0`..`gw11`) | Log SHA-256 |
|---|---|---|
| `b0-s3b` | `78a9e73ba7fc56565c46a148bf4a4467ed469708e26cab2453c1d1f2ea27cace` | `b5dc1c77f7d3a3cd16e750b42eb7f1ba0ca63d86b2db46cc9aa043eb8fc36a5b` |
| `b0-s3` | `6780d30cbdc98dd7c9bee3adc717b8ad556a7696875829eec6a628852b0a0532` | `7f103d00b76d1ef14b572be07a17d47762a3a7b63a02b7b1f0f8f4ff995770ab` |
| `b0-s` | `4947d78e9b5b3db6ddc10788a2d457224f2733020f403f66f0a2510063463793` | `ef775a61f62fc8ca503ba501a49e13ee443658bc48d29c560728f8816c3e26c4` |
| `b0-1` | `60adec761b988e38408ff706ceb58604da5023fed1c5fedb18e7df18f68b334d` | `c853c4855497e05704a2b49428971f06947873f3314bb1a5753d74de445cfda3` |
| `b0-2` | `7f67053a1a69a1686592c53546a4027347e779031953bfa4c433529fd752bafe` | `c3b8e4201f3229f544348e0e50131999bd5f15570080744e070894ff38beaa76` |
| `b0-4` | `66d959e591a5291971603b794d53861b60ff1b3171b4fbf6567f7dd3e0e894c4` | `c3592030bd710668be8481951bb29c76bf2bc1d53478c62f4b86624fcce4f524` |
| `b0-3` | `aecd57397e3b27f9c182fdcfe745c1475b0df07073033b4feade8ac0ad11d2ba` | `83952abbe028aa722f59394f47047f0546e10b51695b3c022e8aa5bd8769a25e` |

### Preserved Skipped Controls and Historical Sibling Context

1. **Current Scoped Skips (2 tests, UNPROVEN on non-elevated Windows):**
   - `tests/test_b0_4_operator.py::test_real_file_symlink_or_unproven` (1 skipped in `b0-4`)
   - `tests/test_b0_3_attended_worker.py::test_actual_file_symlink_when_host_permits` (1 skipped in `b0-3`)
   - Both require Windows file-symlink creation privilege (`SeCreateSymbolicLinkPrivilege` / Developer Mode); native file-symlink rejection on this host remains **UNPROVEN** and is not promoted to `PASS`.
2. **Historical Sibling Records Preserved Untouched:**
   - Prior [`B0_S3_SOURCE_HANDOFF.md`](B0_S3_SOURCE_HANDOFF.md) recorded a broader 30-group diagnostic run with 4017 passed, 19 failed (16 in `b0-3` under heavy 30-group load, 2 in `a05`, 1 in `test_mt01_v21.py`), 23 skipped, and 9 unexecuted Git-mutating onboarding tests.
   - Prior [`B0_S1_S2_SOURCE_HANDOFF.md`](B0_S1_S2_SOURCE_HANDOFF.md) recorded 3654 passed, 7 failed (4 in `b0-3`, 2 in `a05`, 1 in `test_mt01_v21.py`), 23 skipped, and 9 unexecuted onboarding tests.
   - Those historical records remain untouched in the repository; the current 7-group `select_impact` closure for `b0-s3b` observed `54 passed / 0 failed / 1 skipped` on `b0-3` and `68 passed / 0 failed / 1 skipped` on `b0-4`, which does not erase historical broad-suite observations or prove live custody.

---

## 4. Tested Static File Hashes and Post-Executor EF01 Handoff

All six static implementation/contract/test files below remained byte-identical before and after every Round 1 group execution:

| Tested Static File | SHA-256 |
|---|---|
| `docs/security/B0_S3B_BROKER_SELECTION_MATRIX_V1.md` | `4eb4d2d7dcc1403b8fa70d3c0608d3bd71f3d9678778f8b3965e5374c5f57901` |
| `docs/security/B0_S3B_ISOLATION_ATTESTATION_PLAN_V1.md` | `7e66ff5a7dec4e8d77a71c2f5be7ea8ada5300e42dfdbdde138a2ad45264f295` |
| `src/ai_commerce/production_runtime/broker_intake.py` | `fc8726020452eaefcca9b756a1cb71b8008801720c37baedaedd41b1e74984ba` |
| `src/ai_commerce/production_runtime/broker_readiness.py` | `7ac617e5e4cc7af852eaabec184cc589e918b871fb6b826db900ce9675a09062` |
| `tests/test_b0_s3b_broker_readiness.py` | `2ad67314890332dcc80ea77cb7f25c3d168e5530b858c7cdc58fccfc29c87eb4` |
| `work_items/mt01-components/b0-s3b.json` | `fbe9b6787ccc5d9afb60ccd16a1cce5640d3e916c21fd1df2cacc09b1578366c` |

- **New AC Registration State:** `B0-S.3b.broker-selection-readiness` and `B0-S.3b.private-isolation-evidence-gates` are declared in `work_items/mt01-components/b0-s3b.json` (`PENDING_BRAIN_REVIEW` until post-executor EF01 onboarding binding and Brain review on the candidate PR).
- **Post-Executor EF01 & Candidate Release Gate:**
  - `postexecutor_ef01.status`: `PENDING_NOT_RUN_BY_EXECUTOR`
  - `verified_candidate_sha`: `null`
  - Only the outer EF02 worker may publish `CANDIDATE_BRANCH` after authentic post-executor EF01 verification binds all eight final files against `c7ae34c5a670277aaadbd422404fd41a06959d11`, followed by independent hosted Windows PR CI (`-n 4`) and genuine Brain review bound to exact `HEAD`/`base` before trusted merge.

---

## 5. Explicit Next Human Decision Gate (Before Any Live Broker or Custody Claim)

Before any transition beyond `DESIGN_READY_FOR_HUMAN_SELECTION` can be attempted in a future stage:

1. **Human Platform & Vendor Selection:** Human must explicitly choose the off-host broker platform pattern (e.g., `P1` Cloudflare Workers + Durable Objects, `P2` Azure Managed Compute + Key Vault, `P3` Google Cloud Run + Secret Manager, or `P4` Separate Dedicated Device Broker) in a separate authorization.
2. **Human Separate Trusted Display / Authenticator Selection:** Human must select and enroll a separate trusted device for full WYSIWYS transaction display and consent (distinct from Z1 Windows runner and beyond WebAuthn UV alone).
3. **Human Spend Budget & Currency Authorization:** Human must set explicit currency and minor-unit spend ceilings (`0` during no-secret canary trials).
4. **Independent Attacker / Out-of-Band Auditor Evidence:** An independent auditor outside the Z1 Windows SID and outside Z3 self-assertions must verify `G01`–`G10` and `T01`–`T20` (same-SID non-reachability, deploy-admin separation, redacted canary zero-echo, atomic replay/budget durability, and `<=300s` `KILL` latency) before `PRIVATE_CUSTODY_VERIFIED` can leave `UNKNOWN`.
