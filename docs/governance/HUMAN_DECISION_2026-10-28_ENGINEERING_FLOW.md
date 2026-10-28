# Human Decision — Engineering Flow integration into Roadmap A

Date: 2026-10-28 · Repository: `trung-via/ai-commerce` · Status: **HUMAN-ADOPTED INTERNAL PLANNING**.

## Human decisions in conversation

1. AI Commerce remains independent from AIOS-Renew. Reuse **selected engineering techniques/components**, not the AIOS Kernel/Runtime/Publisher, workflows, shared accounts, credentials, or task lifecycle.
2. Build EF using **selective code extraction/adaptation** from an identified AIOS source snapshot instead of rewriting everything; no automatic downstream sync.
3. **EF01 includes both Codex and Antigravity** in the first working core. Manual choice of executor, model, and reasoning effort is supported per WorkItem; advisory recommendation is allowed. Auto-select or cross-executor fallback is not silently enabled. Token/quota/cost claims must reflect provider-visible evidence.
4. **EF02** is independent remote wake and feedback with native continuation optional subject to actual evidence. It does not block creative work.
5. Human directly requests **canonical Work Tree and Roadmap A update** to Phase 0 Engineering Foundation. This decision records planning, not authorization to dispatch code or run executors.

## Canonical artifacts

- `docs/roadmap/WORK_TREE_V1_1.json` and `WORK_TREE_DETAILED_V1_1.md`: 61 baseline WBS IDs preserved, EF01/EF02 added = **63** total (20 P0 / 32 P1 / 11 P2).
- `docs/roadmap/ROADMAP_A_CREATIVE_FIRST_V1_0.json` and `.md`: six selected phases and A01–A18 with Phase 0 EF integration. M0 core and M0-R remote milestones separately described.

## External component adoption note

Potential source `trung-via/AIOS-renew` `main` at `cbefb92612f24b55417165736c63753fc560d403`. This snapshot is **reference only** until implementation. Before importing any code, record individual component source path/version/license or rights status, local owner, dependency modifications, compatibility and rollback. No actual module or credentials were imported by this planning change. Human remains highest authority for internal decisions; outside platform entitlements remain independently factual.
