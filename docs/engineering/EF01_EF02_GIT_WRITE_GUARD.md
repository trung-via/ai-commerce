# EF01/EF02 Git Write Guard — Executor-owned files, Worker-owned Git refs

**Scope:** independent AI Commerce engineering only. Source change proposed for Human review; not merged or production-proven before CI/review.

## Why

EF02 Issue #98 (run 40920589256) demonstrated an executor committing to the isolated worktree, changing HEAD despite EF01's expected fixed source. Existing EF01 source reconciliation correctly returned `GIT_UNEXPECTED_COMMIT / GIT_HEAD_CHANGED` and rejected candidate publication. No relaxation of the source check is permitted.

## Implementation

- Each EF01 invocation prepends an explicit `GIT_GUARD_INSTRUCTION` to the executor prompt. It prohibits commit, checkout, switch, branch, rebase, merge, reset, push, update-ref, stash and Git metadata changes; normal source file edits and read-only Git are allowed.
- A separate evidence-local hook directory is created **per invocation**. `GIT_CONFIG_COUNT / GIT_CONFIG_KEY_n / GIT_CONFIG_VALUE_n` inject `core.hooksPath` **into the executor subprocess environment only**. The hooks include `pre-commit`, `pre-merge-commit`, `pre-rebase`, `pre-push`, and `reference-transaction`, all returning `AI_COMMERCE_GIT_WRITE_BLOCKED`.
- Git for Windows can execute `#!/bin/sh` Git hooks using Git Bash. The hooks do not require project-wide Git configuration or global user settings.
- EF01 itself (the parent), its source snapshots / pytest verification and EF02 Worker/Publisher do not receive that child-only environment. EF02 alone produces create-only candidate refs after independent verification.
- The preexisting **after-executor + after-verify** immutable HEAD/worktree checks remain mandatory. If hooks cannot be prepared, EF01 stops rather than silently launching an unguarded provider.

## Limitations and acceptance

This is a **defense-in-depth guard for normal Git CLI behavior, not an OS-enforced security sandbox**. An executor able to override `core.hooksPath` with a CLI config option, edit `.git` directly, or modify working files through a ref-free operation may bypass Git hooks. Hence the final HEAD, Git worktree identity, protected path and candidate hash verification MUST NOT be removed. Normal `git add` may stage a file but does not modify HEAD; EF01 checks the full worktree diff and does not equate staging to candidate publication.

Acceptance requires the observed Git subprocess tests on Windows and Ubuntu (ordinary commit, `--no-verify`, branch checkout, reset, unaffected parent Git), simulated provider tests for both Codex and Antigravity, normal EF01/EF02 suite, and clean CI. It does not prove behavior for every Git binary override or a malicious provider. A more stringent security boundary would need OS-level process/ACL isolation; this minor change intentionally does **not** alter user accounts, Windows services, the self-hosted runner setup, Git credentials, or model entitlements.

**Rollback:** revert the feature PR; no persistent Git hooks or OS settings are installed.
