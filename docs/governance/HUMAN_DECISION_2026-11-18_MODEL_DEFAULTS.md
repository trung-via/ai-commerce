# Human Decision — AI Commerce Engineering Model Defaults (2026-11-18)

Status: Human-approved new soft-default preference; source change requires PR CI, independent Brain acceptance and merge before it governs new tasks.

## Current selection

| Priority | Executor | CLI model | CLI effort | UI label |
|---|---|---|---|---|
| Primary | codex | `gpt-6.2-sol` | `high` | Codex 6.2 Sol High |
| Quota fallback | antigravity | `gemini-4-hydrogen` | `xhigh` | Gemini 4 Hydrogen Extra |

In Antigravity, **Extra** is the UI name for CLI **xhigh**. This is not the CLI value `extra`; refer to https://antigravity.google/docs/models.

## Preserved selection behavior

1. `hard_default: false`: a complete, explicit Human executor/model/effort override wins for its new attempt.
2. Use the primary for a NEW EF01/EF02 authoring action. Switch future selections to the fallback only on real, determinate `QUOTA_EXHAUSTED` evidence matching the **current exact primary** profile. Unsupported model, timeout, permission denial, capacity or UNKNOWN are not quota evidence.
3. Bind persisted quota state to the exact preferred executor, model and effort. Old Codex 6.1 quota records, including unbound legacy state, must never demote Codex 6.2.
4. Preserve the 3600-second recovery-check cooldown. The next separately authorized new action may probe Codex; only a determinate Codex result resets the preference. Do not replay or mutate an in-flight immutable EF02 attempt.
5. The policy sets a *soft default*, not a ChatGPT session model or an unverified live provider capability. The actual Windows Codex/Antigravity CLI model availability, quota and invocation receipts remain runtime-specific. Existing task-pinned model/effort choices are unchanged.

Source: `config/engineering/model-defaults.v1.json`; resolver: `src/ai_commerce/engineering/model_defaults.py`. This decision supersedes the preferred/fallback model values in the historical 2026-11-02 decision without changing its permission and execution safeguards.
