# Human Decision — AI Commerce soft model preference

Date: 2026-11-02. Status: HUMAN APPROVED policy; runtime activation needs tested code on main.

## Preferred selection (not a hard default)

| Priority | Executor | Exact CLI model | Effort |
|---|---|---|---|
| Preferred | codex | gpt-6.1-sol | max |
| Quota fallback | antigravity | gemini-3.8-flash | high |

A complete, explicit Human choice of executor/model/effort always wins, even while quota fallback is active. The soft default is applied only when authoring a NEW task. An already claimed immutable EF02 packet is NEVER changed, retried or moved to the alternate provider.

## Quota and recovery contract

1. Switch future unpinned task defaults to Antigravity only on real, determinate Codex QUOTA_EXHAUSTED returned by an invoked EF01 process and matching the preferred model/effort. Ignore provider capacity, permission denial, model unsupported, unverified tool text and interrupted/UNKNOWN effects.
2. Save fallback state outside Git under ignored .local/engineering. Quota evidence must include a real bounded action result; record source attempt. Do not infer a reset date from quota text.
3. After a configurable 3600-second cooldown, prefer Codex for the NEXT separately authorized new task as a recovery check, not as proof that its quota has already reset. Only a determinate Codex completion restores the preferred default; a fresh true quota failure returns to fallback. No background polling or vendor-billed quota probes are scheduled.
4. Human override is per task and does not force future task defaults. Do not use failed/side-effect-unknown attempts as automatic fallback triggers for the SAME attempt.
5. EF02 action JSON still requires exact executor/model/effort before claim. This proposal does NOT change immutable EF02 execution or enable model swapping mid-run. This local state is not yet a distributed ChatGPT/GitHub quota ledger; other runners and Brain/plugin authoring must consult trusted evidence rather than assume the state is synced.

## Implementation and acceptance

- Config: config/engineering/model-defaults.v1.json (soft default, not a forced executor).
- Resolver: src/ai_commerce/engineering/model_defaults.py. Read-only check: python -m ai_commerce.engineering.model_defaults --repo PATH.
- EF01 CLI: omitting all selection flags resolves soft default; complete explicit triple overrides; partial triple is rejected. EF01 receipt records true selection provenance; results can update quota state for subsequent EF01 CLI actions.
- Existing EF02 packet schema/validation, sandbox, verifier and xdist worker policy remain unchanged.
- Tests cover primary, Human override, quota-only fallback, conservative UNKNOWN, after-cooldown recovery check, determinate restoration, stale event prevention and rejected partial override. Add tests to an already registered EF01 test family.

Provider entitlement, exact CLI model support, a centralized cross-host quota state, and full post-merge activation remain UNVERIFIED until actually observed.
