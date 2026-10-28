# Human Decision — EF02 Multi-Action Concurrent Execution v1

**Date:** 2026-10-28 · **Project:** independent AI Commerce · **Decision:** HUMAN-APPROVED FOR IMPLEMENTATION.

## Human directive

Human approves upgrading the existing EF02 WBS item (not creating a third Engineering Flow WBS ID) so Human and/or AI Commerce Brain can submit **many engineering actions together**. The EF02 v1 deliverable includes **real concurrent execution of at least two independent actions on Windows**, not merely a queue displaying multiple pending actions. This decision supersedes any earlier "one mutating job at a time" suggestion as an EF02 completion target.

- A batch can contain multiple actions; submissions from Human and Brain can arrive concurrently and must be separately identifiable. One action can depend on other actions (acyclic DAG).
- Within a batch, default `max_parallel=2` **configurable**, not a permanent cap; independent ready actions run concurrently, dependent actions wait for prerequisites. A larger batch is accepted/queued without pretending every action starts immediately.
- Each action binds its exact selected `executor` (Codex or Antigravity), `model`, `effort`, `source_sha`, scope and verification. Recommendations are advisory; no implicit fallback or executor/model switch within an attempt. Human retains overriding authority over project-internal choices.
- Each attempt owns a unique worktree, evidence and candidate ref. Shared Git metadata is protected only for short administrative operations, not by a global lock surrounding entire executor sessions.
- Deduplicate by immutable `attempt_id + request_fingerprint` and distributed claim before executor invocation. Never claim "exactly-once" provider execution when a process can crash after invocation.
- Failures, quota 429, unsupported model, runner loss, unknown side effects, verification and publication failures remain distinct. No silent success or blind provider retry. A repair creates a new attempt and references the earlier attempt.
- Results are returned per action and as an aggregate batch summary to GitHub; Brain can retrieve them through its connected tools. Automatic proactive ChatGPT wake or native multi-turn is a **separate optional proof**, not implied by GitHub feedback.
- EF02 is Engineering Flow, not product job automation; A03/A04 and offline creative are not blocked. No AIOS-Renew Runtime, Kernel, Publisher, credentials, default audit gates or task lifecycle is imported.

This is authorization to **update the canonical EF02 implementation scope and begin development**. It does not assert that a remote runner, workflow, provider invocation, Git publication or Chat Wake is already activated. Implementation will publish its own candidate evidence and verification truth.

## Canonical references

- `docs/engineering/EF02_MULTI_ACTION_IMPLEMENTATION_PLAN_V1_0_2026-10-28.md`
- `work_items/EF02-MULTI-ACTION-001.json`
- `contracts/RemoteActionBatch.v1.schema.json`
- Updated `docs/roadmap/WORK_TREE_V1_1.json` and `docs/roadmap/ROADMAP_A_CREATIVE_FIRST_V1_0.json`.
