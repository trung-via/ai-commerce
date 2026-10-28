# AI Commerce — Living project state

Updated: 2026-10-28. Repository: `trung-via/ai-commerce`.

## Source of truth

- Human-approved Governance v0.3: `CONSTITUTION.md`, `PROJECT_CONTRACT.md`, `docs/governance/HUMAN_ADOPTION_2026-10-27.md`.
- Canonical planning: `docs/roadmap/ROADMAP_A_CREATIVE_FIRST_V1_0.json` and `docs/roadmap/WORK_TREE_V1_1.json`.
- Research reference: Blueprint v1.2; research is not runtime proof.
- A01 implementation is the first *application skeleton*, not activation of EF01/EF02 or product capabilities.

## A01 scope

A01 corresponds to G01/G02/G03/G06/G08: packaging, importable CLI, architecture owners, targeted tests, project isolation.

## Implementation truth

- `A01`: source implemented; **Linux 12/12 targeted tests and Windows PowerShell 12/12 tests observed in Human-supplied screenshot**; see `A01_IMPLEMENTATION_REPORT.md` for verification boundaries.
- `A02`: **BUILD_VERIFIED_FOR_SCOPE (Linux local)** — 5 portable JSON Schema contracts, source/artifact SHA-256, mock provider, typed capabilities, CLI validation and example mock artifact. 41/41 project targeted tests after cross-platform date-time fix; an earlier Windows run showed 30 PASS / 1 FAIL on old code; **the corrected build subsequently showed 41/41 PASS on Windows in a Human-provided screenshot**. Evidence: `A02_IMPLEMENTATION_REPORT.md`.
- `EF01`: **BUILD_VERIFIED_FOR_SCOPE** — 12/12 local synthetic adapter tests and TWO Windows live executor attempts returned `BUILD_VERIFIED_FOR_SCOPE` with `EF01_EXIT=0` in Human-supplied screenshots: Codex `codex-002` (`gpt-6.1-sol/max`), Antigravity `agy-002` (`gemini-3.8-flash/medium`) on source `40558e0ea2392ef306d75af045d5844051c59ef8`. Full local logs and detailed diff not independently read.
- `EF02`: **M0-R ACCEPTED_WITH_DEFERRED_RISKS** (current disposition, 2026-10-28) — 5/5 live DAG, Windows parallel subprocesses, actual EF02→EF01→Codex process interruption + immutable checkpoints; real Windows runner-service outage and rare live negative paths intentionally UNKNOWN/deferred. WorkItem `EF02-MULTI-ACTION-001.json` is authoritative for evidence.
- Creative, media generation, platform posting, analytics, settlement: NOT_IMPLEMENTED / NOT_VERIFIED.

## Unknowns and next steps

- A01 Python foundation installation and targeted tests on Windows: **PASS reported with screenshot evidence (2026-10-28)**. Exact `git rev-parse HEAD` of this workstation and interpreter `python --version` were not shown.
- Executor CLI invocation and selected model eligibility for **Codex gpt-6.1-sol/max** and **Antigravity gemini-3.8-flash/medium**: observed SUCCESS on Windows. The earlier `gpt-6.1` and `gemini-4-argon` attempts failed due to unsupported model and per-model quota respectively; installed CLI versions, billing and quota balances remain UNKNOWN.
- Remote runner installation/permissions and return-to-Brain connection: UNKNOWN.
- Product/media third-party accounts and publishing permissions: UNKNOWN.
- A02 Windows report (2026-10-28): prior revision 30 PASS, 1 FAIL in `test_digest_and_time_format_are_checked`, mock generation succeeded; date-time validator corrected and 41/41 local tests PASS; Windows post-fix retest now PASS per Human-provided screenshot: 41 passed in 2.08s. Exact Windows checkout commit SHA was not shown.
- Next product milestone: **A03/A04 Creative Factory**; optional EF01 Verify–Repair, EF02-D exact-chat wake and Direct Git/pytest performance backlog may proceed in parallel but are NOT gates. M0-R accepted with explicit deferred risks.

No git push, external post, media generation, commercial account linkage or paid API call is implied by any A01 test.

## EF01 implementation update — 2026-10-28

The EF01 standalone CLI and adapters have 12/12 local synthetic targeted tests PASS. Subsequently, Windows live conformance returned `BUILD_VERIFIED_FOR_SCOPE` for `codex-002` (Codex `gpt-6.1-sol`, `max`) and `agy-002` (Antigravity `gemini-3.8-flash`, `medium`), each with `error_code=null`, `EF01_EXIT=0`, source SHA `40558e0ea2392ef306d75af045d5844051c59ef8`, per Human PowerShell screenshots. Earlier `codex-001` (model not supported) and `agy-001` (quota exhausted) failures are retained. M0 core is evidenced for this scope; M0-R / EF02 remains OPEN. Full local attempt JSON/logs/changed-file hashes and exact CLI versions were not independently read. See `EF01_IMPLEMENTATION_REPORT.md`.


## EF01 full Windows regression — 2026-10-28

Human-provided PowerShell screenshot shows successful `git pull --ff-only origin main`, fast-forward `95228f2..ac970fe`, followed by `python -m pytest tests -q`: **53 passed in 5.89s, 0 failed**. Four earlier `WinError 193` failures from POSIX-style mock CLI shebang execution were corrected in `tests/test_ef01.py` by a Windows `.cmd` launcher (commit `ac970feff4914467c9279406003f6582bd2160d5`). Both Codex and Antigravity live attempts had already returned `BUILD_VERIFIED_FOR_SCOPE`. EF01 is complete **for the agreed local dual-executor engineering scope**; EF02 remote wake / M0-R remains NOT_IMPLEMENTED. This is screenshot-supported evidence, not an independently collected pytest log or a GitHub Actions run.


## EF02 Multi-Action implementation approval — 2026-10-28

Human approved update-for-implementation: Human/Brain may submit multiple concurrent engineering actions; EF02 must genuinely run ≥2 independent EF01 subprocesses simultaneously on Windows (initial configurable pool=2), with DAG scheduling, claim/dedup, per-action worktrees and Git candidate refs, plus aggregate GitHub feedback. Submission concurrency and process parallelism are separate claims; single-runner cross-batch jobs may queue. WorkItem `EF02-MULTI-ACTION-001` is ACCEPTED_FOR_WORK; **no EF02 runtime, GitHub Actions workflow or Windows runner has been activated by this planning commit**, and M0-R remains OPEN. Canonical source: `docs/governance/HUMAN_DECISION_2026-10-28_EF02_MULTI_ACTION.md`, `docs/engineering/EF02_MULTI_ACTION_IMPLEMENTATION_PLAN_V1_0_2026-10-28.md`, `contracts/RemoteActionBatch.v1.schema.json`.


## EF02 source implementation — 2026-10-28

GitHub-hosted test run 40648371883 passed on Windows and Ubuntu, including real overlapping Python test subprocesses and fake GitHub API claim races. This proves only source-level plumbing, NOT a remote Codex or Antigravity execution on the user Windows laptop. Remote Windows runner and M0-R remain OPEN. See docs/engineering/EF02_IMPLEMENTATION_REPORT.md.

## 2026-10-28 — M0-R accepted and optional EF enhancements planned (latest disposition)

`main` accepted [PR #50](https://github.com/trung-via/ai-commerce/pull/50) with M0-R `ACCEPTED_WITH_DEFERRED_RISKS`; [Issue #10](https://github.com/trung-via/ai-commerce/issues/10) was closed only for the accepted scope. Evidence includes DAG run 40665011616 and real scoped EF02→EF01→Codex interruption run 40715394122. Runner SERVICE loss/recovery and other live rare negatives remain `UNKNOWN_DEFERRED_UNTIL_REAL_INCIDENT`; no blind replay. Some earlier chronological sections above describe pre-implementation snapshots and must NOT supersede this latest disposition.

Human approved three **planned, not implemented** optional extensions: [EF01-R](../../work_items/EF01-VERIFY-REPAIR-002.json) (P1, bounded verify→repair loop in one EF02 action); [EF02-D](../../work_items/EF02-EXACT-CHAT-WAKE-002.json) (P1, verified exact-chat wake with GitHub fallback); [EF01-P](../../work_items/EF01-DIRECT-GIT-PYTEST-003.json) (P2, optional Git executable + measured pytest-xdist up to 12 workers). Full decision: [EF enhancements](../governance/HUMAN_DECISION_2026-10-28_EF_ENHANCEMENTS.md). No provider invoked, chat wake delivered, 12 workers executed, new runner installed, or changes to original EF01/EF02 runtime by this planning record. Creative Factory A03/A04 remains the next product-facing lane and is not gated by any of these enhancements.
