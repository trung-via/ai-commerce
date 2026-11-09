# HostOps — EF02 attempt #698 one-shot read-only reconciliation

Source authority: Human approval in ChatGPT on 2026-11-09 to implement `EF02_ATTEMPT_RECONCILE_READONLY` for Issue #698. This is a narrow operator; no general shell, no replay, no process kill or restart, no WorkOwner mutation, and no provider, media, PULSE, browser, or publishing effects.

## Fixed invocation contract

This feature is only reachable after its reviewed source and protected `.github/workflows/issue-ingress.yml` are merged on `main`. The authenticated repository owner opens exactly one new Issue titled:

`[HOSTOPS EF02 RECONCILE] #698 attempt-only`

with exactly this UTF-8 JSON body (no markdown code fence or additional keys):

`{"schema":"ai-commerce.hostops.ef02-attempt-reconcile.v1","operation":"EF02_ATTEMPT_RECONCILE_READONLY","issue_number":698,"parameters":{}}`

The protected workflow verifies the original GitHub actor, issue author, title, event, and exact body before running the hard-coded observer on the existing HostOps Windows self-hosted runner. No request field, comment, Issue title suffix, remote URL, program path or user-supplied PID is forwarded as a command.

## Source-locked observer

`src/ai_commerce/hostops_v3/ef02_attempt_reconcile_readonly.py` pins original Issue #698, source SHA, immutable attempt ID, action ID, and EF01 PID 14636. A bounded fixed PowerShell CIM read inspects only the named PID and treats any PID created outside the original launch window or with nonmatching Python command/attempt identity as `PID_REUSED_OR_UNRELATED`. A PID missing today does **not** prove the provider never ran or was without side effects.

The observer looks for at most four exact-action, temporary EF02 worktree matches. It refuses file links and Windows reparse ancestors and reads only five allowlisted BQ-06b source paths plus one optional terminal JSON receipt, with finite size limits. It does not change the checkout, invoke `git status`, clean scratch, collect arbitrary command output, or read unrelated files. A missing temporary workspace is reported as unavailable, not a clean exit or no-write proof. Host-only raw process command lines and paths are never published.

Only the bounded receipt in `RUNNER_TEMP` is written; that ephemeral sanitized receipt is the operator's sole output artifact. The protected GitHub publisher checks an exact schema, field set, enum catalog, source pin and false values for all replay/write/provider-verified flags. It also samples the exact remote WorkOwner ref namespace read-only, reporting only a bounded latest stage (not claiming a verified terminal receipt). The public Issue comment is prefixed `AI_COMMERCE_HOSTOPS_EF02_ATTEMPT_RECONCILE_RESULT_V1`.

## No automatic release of UNKNOWN

Even if native PID and worktree are absent, the historical chain `OWNED → CLAIMED → SPAWN_INTENT → CHILD_OBSERVED → PROVIDER_INTENT` on GitHub still records a possible provider effect. No native process status alone may release the fence. Any new R3 requires separately reviewed authentic WorkOwner/effect reconciliation, a verified outcome or approved recovery policy, a new distinct attempt, exact current `main`, real scoped Windows MT-01 and Brain acceptance.

This feature cannot elevate the archived A09/A11 observations into provider-verified marketing outcomes, nor authorize BQ-06b to mutate PULSE or publishing providers.

## Correction R0.1 — read the actual EF02 evidence directory

Root cause of the prior read blind spot: `LocalBatchWorker.execute_action()` writes the EF01 output at `self.root/<action>-<attempt>/ef01/engineering_result.json`, where `self.root` is `<EF02 checkout>/.local/ef02/<batch_id>`. The original HostOps probe checked `<isolated temporary Git worktree>/ef01/engineering_result.json` instead. These locations are separate. Hence the native Issue #703 category `NO_TERMINAL_RECEIPT_OBSERVED` was **not an examination of the source-owned canonical EF02 result directory**, and never proved no provider return.

The source-only correction pins an exact, non-Issue-configurable EF02-01 runner checkout root from the authenticated run #42572795120, full batch, action and attempt. It checks the canonical terminal only when the original isolated worktree is independently observed; rejects symbolic links, junctions, reparse ancestors, nonregular/hardlinked file, changed file identity, oversized JSON and mismatching `work_id`/`attempt_id`. The public contract and GitHub publisher stay unchanged. It reports only `LOCAL_RESULT_PRESENT_UNVERIFIED`, `RECEIPT_READ_UNAVAILABLE` or `NO_TERMINAL_RECEIPT_OBSERVED`, never private JSON contents or local directory names.

No source-only verification proves the file still exists or that the provider returned. Hosted Windows CI tests mock an isolated fixed layout. A **subsequent** native HostOps read through the existing exact source-owned Issue contract, after independent PR review and trusted merge, is necessary. Source branch, test fixture and GitHub issue are not a WorkOwner transfer or replay grant. The result always remains `HOLD_UNKNOWN_EFFECT`, `replay_authorized=false`. If the action evidence folder was cleaned or moved, preserve UNKNOWN and design separately approved private read authority rather than guessing or retrying Gemini.
