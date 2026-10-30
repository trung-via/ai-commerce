# EF01/EF02 Git Write Guard — Executor-owned files, Worker-owned Git refs

**Scope:** independent AI Commerce engineering only. Source change proposed for Human review; not merged or production-proven before CI/review.

## Why

EF02 Issue #98 (run 40920589256) demonstrated an executor committing to the isolated worktree, changing HEAD despite EF01's expected fixed source. Existing EF01 source reconciliation correctly returned `GIT_UNEXPECTED_COMMIT / GIT_HEAD_CHANGED` and rejected candidate publication. No relaxation of the source check is permitted.

## Implementation

- Each EF01 invocation prepends an explicit `GIT_GUARD_INSTRUCTION` to the executor prompt. It prohibits commit, checkout, switch, branch, rebase, merge, reset, push, update-ref, stash and Git metadata changes; normal source file edits and read-only Git are allowed.
- A separate evidence-local hook directory is created **per invocation**. `GIT_CONFIG_COUNT / GIT_CONFIG_KEY_n / GIT_CONFIG_VALUE_n` inject `core.hooksPath` **into the executor subprocess environment only**. The hooks include `pre-commit`, `pre-merge-commit`, `pre-rebase`, `pre-push`, and `reference-transaction`, rejecting protected writes with `AI_COMMERCE_GIT_WRITE_BLOCKED`.
- The runner observes the source's physical worktree, Git directory and common Git directory using read-only `rev-parse`. Hooks reject writes to any overlapping identity, including linked worktrees sharing the common directory. An initialized disposable fixture repository can proceed only after all three of its physical directories are independently observed and disjoint. Parent verification prepares fixtures; missing/bare/ambiguous identities, including unfinished initialization, deny writes. Nested guards retain ancestor identities; an unidentifiable inherited hooks policy stays block-all.
- The scoped hook uses an invocation-local copy of the Python policy and identity manifest. It neither clears inherited Git configuration nor changes repository/global configuration. An already-running executor with an older block-all hook cannot adopt this policy by overriding its environment; EF01 must verify the patch from its parent environment.
- Git for Windows can execute `#!/bin/sh` Git hooks using Git Bash. The hooks do not require project-wide Git configuration or global user settings.
- EF01 itself (the parent), its source snapshots / pytest verification and EF02 Worker/Publisher do not receive that child-only environment. EF02 alone produces create-only candidate refs after independent verification.
- The preexisting **after-executor + after-verify** immutable HEAD/worktree checks remain mandatory. If hooks cannot be prepared, EF01 stops rather than silently launching an unguarded provider.

## Limitations and acceptance

This is a **defense-in-depth guard for normal Git CLI behavior, not an OS-enforced security sandbox**. An executor able to override `core.hooksPath` with a CLI config option, edit `.git` directly, or modify working files through a ref-free operation may bypass Git hooks. Hence the final HEAD, Git worktree identity, protected path and candidate hash verification MUST NOT be removed. Normal `git add` may stage a file but does not modify HEAD; EF01 checks the full worktree diff and does not equate staging to candidate publication.

Acceptance requires the observed Git subprocess tests on Windows and Ubuntu (ordinary commit, `--no-verify`, branch checkout, reset, unaffected parent Git), simulated provider tests for both Codex and Antigravity, normal EF01/EF02 suite, and clean CI. It does not prove behavior for every Git binary override or a malicious provider. A more stringent security boundary would need OS-level process/ACL isolation; this minor change intentionally does **not** alter user accounts, Windows services, the self-hosted runner setup, Git credentials, or model entitlements.

**Rollback:** revert the feature PR; no persistent Git hooks or OS settings are installed.

## Repair evidence and fail-closed provenance

The #140 repair starts from `9475a3a8f7cee5ee4ea6a7fea24a22c31e927138`, without reusing #138's unpublished worktree or requiring #137's artifact download. The original fan-in receipt and five fingerprint-tampering regressions pass on this source in the current executor. Malformed plan/group/binding shapes are also deterministic missing coverage: EF02 returns `UNPROVEN / AGGREGATE_FRESH_COVERAGE_UNCONFIRMED` before publication, rather than allowing a receipt parsing exception to become `EXECUTION_UNKNOWN`. Child interruption, nonzero exit with a success receipt, and unknown provider effects retain their existing classifications and never authorize replay.

Antigravity's read-only `EF02-Antigravity-Headless-Preflight.ps1` continues to report only sanitized settings presence and read-file ask/deny flags. Full settings and credentials are not copied. Denied-tool evidence still fails closed, including exit-zero/SUCCESS responses; this repair grants no `read_file` permission and changes no Antigravity user/global settings.

Initial local repro: `python -m pytest tests/test_ef01_git_write_guard.py tests/test_minimum_test.py -q` returned **61 passed, 6 failed**. All six failures occurred during disposable `git init`: the inherited hook's Git Bash failed with `NtCreateDirectoryObject ... 0xC0000022`, before policy execution. No hook/environment override is an acceptable verification workaround. Real Git subprocess acceptance remains dependent on EF01 verification in a supported parent environment; no candidate may be published on the strength of mocked policy checks alone.

Focused policy/MT01 verification returned **84 passed, 7 deselected** (the seven real Git cases require parent verification). Round 1 of `python -m pytest tests -q -n 12` returned **1948 passed, 37 failed, 15 skipped** in 260.83 seconds: 36 fixture-initialization failures and one credential-isolation test that incorrectly assumed no inherited Git configuration. That assertion now checks that fetch credentials do not enter the child while inherited guard configuration remains intact. It does not clear the guard environment.

Final focused verification (policy, MT01, performance and EF02 interruption tests, excluding the seven real Git cases) returned **144 passed** with 12 workers. Round 2 of the exact full-suite command returned **1949 passed, 36 failed, 15 skipped** in 254.89 seconds, exit 1. All remaining failures are fixture `git init` exit 128 under the inherited hook; no MT01 fan-in failure remains. The skips reflect host symlink/hardlink restrictions. The two-round limit is exhausted: no environment bypass or third full-suite retry was attempted. The seven changed source/test/documentation paths and unchanged source HEAD were inspected; `git diff --check` passed. This is an uncommitted local patch requiring EF01 parent verification, **not a verified or published candidate**.
