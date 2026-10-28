# EF02 — Multi-Action Remote Wake & Feedback | Implementation handoff v1.0

Date: 2026-10-28. Repo: `trung-via/ai-commerce`. Human-approved technical scope; **implementation is NOT yet active**. Depends on completed EF01 (Codex `gpt-6.1-sol/max` and Antigravity `gemini-3.8-flash/medium` live checks, 53/53 Windows tests; see EF01 WorkItem). This is **Engineering**, not Commerce product orchestration.

## Outcome and non-goals

**Outcome:** Human/Brain submit one batch of independent and dependent actions; EF02 accepts it, chooses READY actions using a validated DAG and a bounded local scheduler, invokes EF01 in **at least two genuinely overlapping Windows subprocesses** when two independent actions are ready, preserves separate worktrees and candidate refs, and provides both per-action and aggregate feedback readable via GitHub.

Remote dispatch can accept multiple separate batches concurrently; on a single Windows self-hosted runner, **different batch jobs can still wait in the GitHub queue**. v1's hard acceptance for actual parallelism is inside a batch (two overlapping EF01 processes), NOT an unsupported claim of cross-batch true parallelism. If simultaneous execution across separate batch jobs becomes necessary, scale to separate runner instances with a host-wide resource semaphore and independently isolated clones; preserve the same claim/evidence identities. No Redis/DB, no auto merge to `main`, no inherited AIOS runtime, no automatic ChatGPT wake requirement. CLI repair is **a new attempt**, not native continuation.

## Selected v1 topology and why

`Human/Brain → authenticated GitHub batch request/Issue → GitHub-hosted validation → GitHub Actions Windows job (project-only runner) → EF02 local pool (2 configurable slots) → per-action EF01 → candidate refs + per-action evidence → aggregate GitHub feedback → Brain reads`.

- Keep one registered **AI Commerce** Windows runner initially; the job runs a scheduler with concurrent subprocesses per batch. This provides real parallel work with less operational coupling than registering many persistent agents at once.
- Each attempt uses a detached worktree at its exact requested source SHA. Only worktree administrative operations, claim I/O and candidate reference publication need short locks/atomic operations. Never hold a host-wide lock while Codex/Antigravity generate code.
- With one runner, submissions across separate batches are admitted/queued, but the batches do not execute concurrently until another runner or a persistent pooled service is added. This limitation must be visible to Human.
- GitHub `concurrency.queue: max` (where supported) is for queueing, NOT idempotency: its group can hold at most 100 pending jobs; overflow must be reported, not silently lost. GitHub-hosted validation must be separated from privileged execution. Event-authorship/authorization and issue-body data are validated before reaching Windows.
- Prefer project-only per-attempt Git worktrees/locations, never share AIOS credentials or Windows runner registration. Use no shell interpolation of Issue content; verification request uses a registered verification profile, not arbitrary remote shell.
- Only an immutable per-attempt distributed claim (`refs/heads/ef02/claims/<hashed-attempt-key>`) determines the winner. A claim collision with matching fingerprint is DUPLICATE, with different fingerprint is CONFLICT. An artifact marker is evidence, **not** a claim lock.
- Candidate refs `refs/heads/ef02/candidate/<hashed-attempt-key>` are **create-only**; reject collisions. Never auto-write `main` or auto-merge mutually conflicting changes.

## Batch request semantics

Contract: `contracts/RemoteActionBatch.v1.schema.json`. Actor/authorization must be tied to the actually authorized GitHub initiating identity or a project-controlled dispatch credential; a plain text `authorization_ref` by itself is not proof of permission. Human/Brain have distinct actor identities and decision provenance. Semantic validation beyond JSON schema must enforce unique `action_id` and `attempt_id`, known dependencies, DAG acyclicity, validated repository-relative paths, verified source ref, batch fingerprint, per-attempt immutability and role authorization.

- `batch_id`: identity for a Human/Brain submission; multiple batches may be in flight.
- `action_id`: logical action inside a batch; may depend on others.
- `attempt_id`: unique invocation lineage, pinned to `request_fingerprint`; another model/profile/repair requires a new attempt.
- `dispatch_id` / `GITHUB_RUN_ID` / `GITHUB_RUN_ATTEMPT`: transport delivery, **not** attempt identity.
- `max_parallel`: concurrency preference; default 2; host resource/quota slots are separately configurable. A larger number is an intention, not evidence the host can run it.
- `depends_on`: in-batch action references; a failed prerequisite makes dependents `BLOCKED_DEPENDENCY` unless Human explicitly overrides by creating a new request. Independent siblings continue.
- `resources`: per-action hints/locks (e.g. provider account, hardware class, shared external resource). Identical source paths do not inherently prohibit parallel worktree execution; conflicts are reconciled at candidate integration.
- `priority` controls READY ordering; Human overrides any advisory Brain priority.
- `delivery` initially `CANDIDATE_BRANCH`; no main publication in EF02 v1.

Example validated transport document:

```json
{
  "kind": "AI_COMMERCE_REMOTE_ACTION_BATCH",
  "version": 1,
  "batch_id": "A03-BATCH-001",
  "source_sha": "40558e0ea2392ef306d75af045d5844051c59ef8",
  "actor": "HUMAN",
  "authorization_ref": "human-directive-2026-10-28",
  "max_parallel": 2,
  "actions": [
    {
      "action_id": "brief",
      "attempt_id": "a03-brief-001",
      "executor": "codex",
      "model": "gpt-6.1-sol",
      "effort": "max",
      "instruction": "Implement a bounded brief builder",
      "allowed_paths": ["src/ai_commerce/briefs"],
      "verification_profile": "briefs-focused",
      "depends_on": [],
      "priority": 50
    },
    {
      "action_id": "assets",
      "attempt_id": "a03-assets-001",
      "executor": "antigravity",
      "model": "gemini-3.8-flash",
      "effort": "medium",
      "instruction": "Implement a separate bounded asset validator",
      "allowed_paths": ["src/ai_commerce/assets"],
      "verification_profile": "assets-focused",
      "depends_on": [],
      "priority": 40
    },
    {
      "action_id": "integrate",
      "attempt_id": "a03-integration-001",
      "executor": "codex",
      "model": "gpt-6.1-sol",
      "effort": "max",
      "instruction": "Integrate accepted action results after reconciliation",
      "allowed_paths": ["src/ai_commerce/integration"],
      "verification_profile": "integration-focused",
      "depends_on": ["brief", "assets"]
    }
  ]
}
```

This example is descriptive: source SHA and verification profiles must match the actual selected source/configuration before execution, and dependency integration may need a rebased candidate source. This document is not an execution request.

## Scheduler and worktree / Git rules

1. Ingress validates the request and authorization, computes a canonical fingerprint for every action and an attempt key based on a stable hash of repo identity + attempt ID. Record accepted, rejected or duplicate requests.
2. On Windows, validate and atomically claim each READY attempt before entering EF01. Same attempt cannot be invoked twice even if workflow is rerun. Claim remains after crash; do not steal it or infer execution completed.
3. Maintain a bounded pool of concurrent **processes**, default 2, not threads pretending to run parallel jobs. When one process finishes, schedule another READY action if its dependency states allow it; honor explicit cancellations and resource constraints.
4. A process gets its own worktree, evidence directory, timeout and selected executor/profile. Its verify profile resolves to trusted argv from the checked-out source; do not interpolate an untrusted Issue body into PowerShell.
5. A passed EF01 result must have expected source SHA, unchanged Git HEAD during execution, allowed changes, targeted verification exit 0 and stable candidate hashes. Build a candidate commit after PASS and publish create-only to an attempt-specific ref. Do not merge siblings or dependency candidates silently. A dependent action must consume pinned upstream candidate identities **only after explicit DAG reconciliation**.
6. Emit a per-action result regardless of success/failure where the runner still controls execution. Preserve raw logs/diffs as artifacts without arbitrary 24KiB truncation. Publish aggregate manifest with totals, FAILED/UNKNOWN/BLOCKED and candidate links; batch success means every required action succeeded, never just GitHub workflow green.
7. If a runner goes offline or a provider's side effect might have occurred, mark `EXECUTION_UNKNOWN`/`CLAIMED_NO_RECEIPT` and reconcile; do not blind-retry provider. New authorized repair is a new `attempt_id` referencing `repair_of`. GitHub API publication retries must not invoke model again.

Canonical high-level status: `RECEIVED → VALIDATED → QUEUED → READY → CLAIMED → EXECUTING → VERIFYING → CANDIDATE_READY → RESULT_PUBLISHED`. Error/exception states: `REJECTED`, `CONFLICT`, `DUPLICATED`, `BLOCKED_DEPENDENCY`, `CANCELED`, `MODEL_UNSUPPORTED`, `QUOTA_EXHAUSTED`, `VERIFICATION_FAILED`, `EXECUTION_UNKNOWN`, `RESULT_PUBLICATION_FAILED`. UNKNOWN is never PASS.

## Implement directly as three bounded increments

| Increment | Engineering deliverables | Evidence |
|---|---|---|
| **EF02-A — Concurrent ingress / identity** | Batch + result/claim contracts; GitHub trusted-event validation; DAG validation; fingerprint; attempt distributed Git claim; batch receipt; multi-batch ingress preserving requests. | 5+ valid actions accepted; malformed/duplicate/conflicting requests rejected deterministically; one claim winner among concurrent contenders; no model invoked during race checks. |
| **EF02-B — Windows parallel executor** | Project Windows runner bootstrap, worktree management, bounded process pool, resource-aware READY scheduling, EF01 adapters reuse, per-attempt candidate refs. | At least **2 independent actions overlap in wall-clock execution** in distinct worktrees; a 5-action batch drains (including dependencies); selected model/effort preserved and no scope crossing; verify existing 53 regression tests with new tests. |
| **EF02-C — Results / recovery** | Per-action artifacts, batch aggregator, GitHub Issue status, bounded diagnostic reader, safe publication retry, repair lineage and kill/crash reconciliation. | One real remote invocation + per-action and batch receipts; GitHub feedback readable by Brain; duplicate/restart/quota/error/UNKNOWN cases preserve facts; no model rerun for publication failure. |

**EF02-D optional later:** automatic proactive Chat Wake and true native multi-turn, separately demonstrated, never inferred from GitHub Issue comments or a second CLI invocation.

## Acceptance cases for M0-R

- One batch of five actions has three independent READY actions and two dependent actions; **two work simultaneously** and aggregate correctly resolves completion vs dependency failure. Record wall-clock spans and runner process IDs proving overlap; not just concurrent queue admission.
- Human and Brain submit independent requests close in time: both are durably received (no default GitHub pending replacement), and wait/order state remains visible if the single runner is busy. Cross-batch overlap is optional v2, not falsely claimed.
- Codex + Antigravity can operate in separate worktrees concurrently where provider quotas allow; negative quota/model cases return honest receipts. Simulated subprocesses establish concurrency even if live provider entitlement varies.
- Same `attempt_id` sent at once 10+ times: only one claimant can run EF01; same ID/different payload produces hard conflict.
- Git candidate collisions do not overwrite existing branches; verify action scope after tests; no arbitrary automatic main merge.
- Failure of an action does not cancel independent siblings; dependencies blocked with explicit reason; cancellation and interrupted execution are not reported as successful.
- Remote feedback available via GitHub and retrievable by Brain, including full diagnostic artifact reference and batch summary.
- Windows runner live installation/version and GitHub event path must be verified separately before claiming M0-R LIVE. Script-level tests or documentation alone do not close M0-R.

## Implementation boundaries / sources

- Canonical EF01: `src/ai_commerce/engineering/`, `work_items/EF01-DUAL-EXECUTOR-001.json`. Do not change its successful invocation semantics or pretend local fake-CLI tests prove a remote transport.
- New namespace proposed: `src/ai_commerce/remote/`, `tests/test_ef02_*.py`, `.github/workflows/ef02-batch.yml`, `scripts/EF02-Windows-Runner.ps1`. No such runtime is claimed implemented by this plan.
- Read official guidance: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax ; https://docs.github.com/en/actions/concepts/runners/self-hosted-runners ; https://docs.github.com/en/rest/git/refs ; https://git-scm.com/docs/git-worktree.
- Rollback: disable EF02 workflow, stop the AI Commerce runner, and revert only EF02 modules/contracts/workflow changes; EF01 and product A01/A02 remain independent.
