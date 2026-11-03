# Windows-only repository audit and cleanup — 2026-11-03

## Scope and source

Repository `trung-via/ai-commerce`, initially audited on PR #301 at commit `855359cdf0cf833fc71dc8bfc0700af1f61535ac` (base main `fa3f8032a1dc2797123dc0a1e8194689402003c4`).

Machine-observed Windows hosted CI: [EF02 source tests run 41608799871](https://github.com/trung-via/ai-commerce/actions/runs/41608799871), job `125825209986`, `Whole-repository Windows-only policy audit` PASS, followed by MT-01 Python and Node checks PASS. No remote EF02 dispatch or live Windows self-host execution is claimed.

The test `tests/test_pr_merge_gate.py::test_repository_wide_windows_only_audit` enumerates **all `git ls-files` entries**, reads every UTF-8 text file, excludes only binary/unsupported files, scans all active workflow definitions for Ubuntu/Linux/macOS runners and Linux shell/install primitives, checks operational source/control scripts for retired dual-OS gate identifiers, and rejects hosted `-n 12` where detected. This test runs in the existing Windows source CI job without starting a second workflow.

## Observed inventory (initial audit commit)

| Item | Observed |
|---|---:|
| Git-tracked files | 703 |
| Readable UTF-8 text files | 687 |
| Binary/non-text files skipped | 16 |
| Active GitHub Actions workflow files | 22 |
| Blocking active Ubuntu/Linux CI dependencies | **0** |
| Informational source/test/embedded-doc references | 27 |
| Informational documentation/archived references | 129 |

**Result: `WINDOWS_ONLY_ACTIVE_CI_CONFIG_VALIDATED`**, not a claim that every historical string or every Linux compatibility branch is deleted.

## Remnants, classified

1. **Active CI and trusted merge gate — CLEAR.** No `ubuntu-latest`, Linux Bash shell, apt-get setup or required dual-OS CI artifacts. Exact HEAD Windows pytest/Node, MT-01 coverage/receipt and Brain decision checks are retained. Hosted Windows uses the source-CI 4-worker setting; real 12-worker and live HostOps/Chrome verification remain Windows self-host only.
2. **Historical documentation — RETAIN, annotate.** Old A-series implementation reports and dated CI receipts contain actual Ubuntu results. Those records are evidence of earlier runs, not current requirements. README, `PROJECT_STATE.md`, and the Issue #209 MT-01 note now distinguish active Windows-only policy from those snapshots.
3. **Historical component verification JSON — RETAIN.** Several A10 `verification.json` files record whether Ubuntu CI was attempted or UNKNOWN for their original worktrees, including AC09 audit entries. Rewriting such retained evidence would misstate history; they do not specify an active GitHub Actions runner.
4. **Portable IPC compatibility path — NOT AN ACTIVE UBUNTU JOB.** `src/ai_commerce/hostops_v3/broker.py` still contains an alternate Linux AF_UNIX/SO_PEERCRED path, while the deployed Windows code selects named pipe/SID peer checks. Removing this privileged security path is a separate source-scope refactor requiring relevant HostOps tests and explicit review, not routine CI noise cleanup.
5. **Tests and negative assertions — RETAIN.** The literal `ubuntu-latest` in the policy test is used to **reject** any reintroduction in active workflows. These strings are defensive checks, not runnable Linux jobs.

## Cleanup delivered in PR #301

- Published the current Windows-only CI/self-host policy at the start of `README.md`; prior Ubuntu counts remain correctly attributed to old runs.
- Annotated dated `docs/engineering/PROJECT_STATE.md` and `docs/engineering/MT01_CI_EFFECTIVENESS_209.md` as historical where necessary.
- Added a continuing full-repository audit test to the existing Windows EF02 source CI; no dedicated Ubuntu matrix or extra job.
- Cleaned the last trusted Brain auto-merge workflow comment that still described obsolete two-OS CI; enforce that deprecated wording in active workflows as a regression failure.
- Kept signed HostOps authorization, immutable EF02 evidence, Brain PASS and exact-head merge prerequisites unchanged.
- Did **not** delete old Actions receipts, historical reports, worktrees, GitHub branches or other projects, and did **not** invoke HostOps/Wake/EF02 live operations.

## Acceptance boundaries

`CONFIG_VALIDATED`: workflow coverage and active OS-routing policy checked from tracked repository bytes and Windows CI.

`LIVE_E2E_VERIFIED`: **NOT CLAIMED**; no Windows laptop self-host deployment or 12-worker measurement is performed by this audit.

Review each subsequent PR using the current trusted main gate, not the superseded Ubuntu+Windows instructions. Any newly found operational Linux route must be evaluated by its actual runtime effect and owner before removal.
