# EF01 — Selective Extraction & Dual Executor Working Core

**Date:** 2026-10-28. **Repo:** `trung-via/ai-commerce`. **Work item:** `EF01-DUAL-EXECUTOR-001`.

## Scope implemented

- Independent Python package `ai_commerce.engineering` with one manually selected executor per work/attempt (`codex` or `antigravity`), exact model and reasoning effort, no hidden defaults or provider fallback.
- CLI `python -m ai_commerce.engineering` invokes one native process, then a selected targeted verification argv, records a normalized `AI_COMMERCE_ENGINEERING_RESULT` and full local stdout/stderr/verify logs.
- Git clean-source preflight, immutable source HEAD, observed modified/untracked paths, optional allowed path scope, no automatic stage/commit/push.
- Provider-reported token usage only when exact numeric counters occur in a recognized output; otherwise `UNKNOWN`. Cost always `UNKNOWN` without provider billing evidence. No 24-KiB diagnostic truncation.
- Explicit failures for missing binary, timeout, nonzero exit, no code change, out-of-scope changes, unexpected Git commit, failed targeted verification, and dirty worktree. No automatic retry and no automatic executor switch.

EF01 is **engineering code execution**, not Commerce product job orchestration or EF02 remote wake.

## Selective adoption ledger (no dependency on AIOS)

Source reference pinned at `trung-via/AIOS-renew@cbefb92612f24b55417165736c63753fc560d403`, Human engineering-flow decision `docs/governance/HUMAN_DECISION_2026-10-28_ENGINEERING_FLOW.md`.

| Pinned AIOS source | Blob SHA | Local adapted concept | Change and dependencies |
|---|---|---|---|
| `src/aios_renew/execution_profile.py` | `c572cdc4682fc6c76db0e5a0c1b6bd60d60b2f4e` | `engineering/profile.py` | Reimplemented minimal explicit profile and model syntax; removed YAML, policy defaults, Run IDs |
| `src/aios_renew/codex_adapter.py` | `92ac8956c607bec204a55eff10c86078b0a8c427` | `engineering/adapters.py` | Adapted `codex exec` argv shape; no Runtime Task, result schema or live transport; `workspace-write` sandbox |
| `src/aios_renew/antigravity_adapter.py` | `8bab9b52c383f29291989a4223cfcef4fa26b9af` | `engineering/adapters.py` | Adapted `agy --print` flag surface; no AIOS pretool guard, permission skip, transport or task serialization |
| `src/aios_renew/run_observation.py` | `0452a7d6335de7bea285c6b13f8048efaa4db8c3` | `engineering/runner.py` | Reimplemented narrow trusted token usage observation; no Runtime RUN/soft budgets |

**Rights/license:** Separate licensing statement for pinned AIOS source not verified; no entire file or proprietary third-party secrets copied. Techniques and small command constructions adapted from Human-selected first-party project. If broader copying is proposed, verify rights for those components separately. **Owner:** AI Commerce Engineering Foundation. **Rollback:** revert EF01 commit(s), remove `src/ai_commerce/engineering/` and its work item/docs; A01/A02 and AIOS source stay unchanged. **Compatibility:** Python 3.11+ intended, shell-free subprocess; Windows live conformance subsequently observed for both CLI paths (see dated result below).

## Local verification actually observed

Isolated local EF01 tests using fake CLI subprocesses and temporary real Git repos: `12 passed` (Linux Python environment). This establishes deterministic command building/result plumbing and negative states, **not live Codex or AGY execution**, Windows host compatibility or installed model entitlement. Existing A01/A02 41-test project suite was not re-run in the actual GitHub repository from this environment. Previous 41-pass Windows A02 evidence is independent.

## Windows live acceptance (run separately for each executor)

Run from a clean checkout of `trung-via/ai-commerce` after the EF01 code is available; example repo path `C:\TOOL\ai-commerce`.

1. Install: `python -m pip install -e ".[dev]"`; check `codex --version` and `agy --help`. Do **not** infer Antigravity availability from an AIOS run or from Codex presence.
2. Put an authorized bounded change instruction in `C:\TOOL\ai-commerce\.local\ef01\instruction.txt`. Put a JSON argv verification command in `.local\ef01\verify.json`, e.g. `["python","-m","pytest","tests/test_ef01.py","-q"]` if verifying infrastructure; for code change, target tests for the actual changed module.
3. Select manually: `python -m ai_commerce.engineering --repo C:\TOOL\ai-commerce --work-id EF01-LIVE-CODEX --attempt-id codex-001 --executor codex --model <actual-codex-model> --effort <actual-effort> --instruction-file .local\ef01\instruction.txt --verify-json .local\ef01\verify.json --out-dir .local\ef01\codex-001 --allow-path src --allow-path tests`.
4. For Antigravity, use a **separate clean Git worktree** (or commit/reset the prior test change first), a new attempt/output folder, and explicit `--executor antigravity --model <actual-agy-model> --effort <actual-effort>`. CLI flags mirror a pinned AIOS native adapter and still require actual installation-level compatibility confirmation.
5. Preserve both local `engineering_result.json` files, full error diagnostics, `git rev-parse HEAD`, `git diff --stat`, Git candidate SHAs, and actual targeted test results. `BUILD_VERIFIED_FOR_SCOPE` for a live attempt requires a changed file and verification return code 0. Two real executor attempts are required to claim full EF01 live acceptance.

**Not included:** EF02 GitHub/Windows remote wake, native app-server continuation, automatic rerouting, project-runner registration, cross-project credentials, provider billing, creative media generation, posting, or publication.

## Windows conformance helper

After switching to the EF01 branch, use [`scripts/EF01-Windows-Live-Conformance.ps1`](../../scripts/EF01-Windows-Live-Conformance.ps1) with explicit `-Executor`, `-Model`, `-Effort`, `-AttemptId` for **one provider at a time**. The script creates a separate clean detached worktree for each attempt, writes a small real pytest change instruction and records local evidence under ignored `.local/ef01-results/`. Run twice (Codex and Antigravity); the script neither commits nor dispatches remotely. Inputs accept PowerShell 5.1 UTF-8 BOM. The script was subsequently run on Windows; observed results follow.


## Windows live conformance results — 2026-10-28 (Human screenshots)

Source checkout before both successful attempts: `40558e0ea2392ef306d75af045d5844051c59ef8` (`main`). The Human supplied PowerShell terminal screenshots; the tool-generated local `engineering_result.json`, stdout/stderr, verification logs and Git hashes remain on the Windows computer and **have not been directly inspected from this repository**.

| Attempt | Executor | Model | Effort | Console status | error_code | EF01_EXIT | Local evidence |
|---|---|---|---|---|---|---|---|
| `codex-002` | codex | `gpt-6.1-sol` | `max` | `BUILD_VERIFIED_FOR_SCOPE` | null | 0 | `.local/ef01-results/codex-002` |
| `agy-002` | antigravity | `gemini-3.8-flash` | `medium` | `BUILD_VERIFIED_FOR_SCOPE` | null | 0 | `.local/ef01-results/agy-002` |

Two preceding attempts correctly produced non-PASS results: `codex-001` with unsupported `gpt-6.1` (HTTP 400 `model_not_found`), and `agy-001` using `gemini-4-argon` (HTTP 429 individual quota reached). Different selected models were used for new attempts, not silent auto-fallback within an attempt. The captured nonzero statuses did not masquerade as PASS.

**Interpretation:** The runner's `BUILD_VERIFIED_FOR_SCOPE` is emitted only after observed code changes within allowed paths and a zero-exit targeted verify command under the checked-out source; the screenshots show this normalized terminal status and `EF01_EXIT=0` for BOTH live executors. This is therefore sufficient for a screenshot-grounded EF01 BUILD_VERIFIED_FOR_SCOPE claim. It is not independent reinspection of their underlying Windows file diffs, precise CLI versions, or provider token/cost/quotas. M0 core is evidenced for this engineering scope, while EF02 / M0-R remains **NOT_IMPLEMENTED**. There is no claim of GitHub CI success, native multi-turn continuation, product provider execution, media or posting.


## Windows regression closure — 2026-10-28

On the actual Windows PowerShell host, the original 53-test run showed 49 passed, 4 failed with `WinError 193`; all four failures were in `tests/test_ef01.py` fake CLI subprocess tests because a Linux-style shebang executable script was not a valid Win32 application. Commit `ac970feff4914467c9279406003f6582bd2160d5` changed only the test fixture to create a `.cmd` shim invoking `sys.executable` on Windows, preserving POSIX handling and leaving production Codex/Antigravity adapters unchanged.

A subsequent Human-supplied PowerShell screenshot shows successful fast-forward pull to `ac970fe` and `python -m pytest tests -q`: **53 passed in 5.89s; 0 failed**. This is a terminal screenshot observation, not raw pytest file/CI confirmation. Together with prior live `codex-002` and `agy-002` PASS screenshots, this **closes EF01's scoped engineering verification**. Exact CLI binary versions and original attempt-by-attempt log files are still outside this repository; no need to rerun paid model work solely to close the known Windows mock-fixture defect. EF02 remains separate and unimplemented.
