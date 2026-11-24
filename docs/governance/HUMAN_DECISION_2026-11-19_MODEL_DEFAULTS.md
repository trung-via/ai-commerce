# Human Decision — AI Commerce Engineering Model Defaults (2026-11-19)

Status: Human-approved model selection; source activation requires Windows PR CI, independent Brain acceptance and merge.

## Preferred execution profile

| Priority | Executor | CLI model | CLI effort | UI label |
|---|---|---|---|---|
| Preferred | antigravity | `gemini-4-neon` | `high` | Gemini 4 Neon High |
| Quota fallback | codex | `gpt-6.2-sol` | `high` | Codex 6.2 Sol High |

This 2026-11-19 decision supersedes the ordering and model values from the 2026-11-18 decision; historical documents remain as audit trail.

## Scope and guarantees

1. Apply this soft default only to new EF01/EF02 authoring with no explicitly pinned complete executor/model/effort triple. A Human-pinned selection still wins.
2. Fall back to Codex only after a real determinate `QUOTA_EXHAUSTED` response from the exact preferred Antigravity model/effort. No switch on timeout, capacity, permission, unsupported model, or UNKNOWN. No mid-attempt swap or replay.
3. Bind quota state to preferred executor+model+effort, so legacy Codex quota evidence does not demote Gemini Neon.
4. After a 3600-second cooldown, the next authorized fresh task can test preferred Antigravity availability. Restore Antigravity preference only on a determinate provider return.
5. Runtime presence and entitlement for `gemini-4-neon` on the Windows host are not implied by this source decision; verify actual CLI invocation receipts separately.

Source of truth: `config/engineering/model-defaults.v1.json`; resolver: `src/ai_commerce/engineering/model_defaults.py`. The source policy does not change the ChatGPT session model.
