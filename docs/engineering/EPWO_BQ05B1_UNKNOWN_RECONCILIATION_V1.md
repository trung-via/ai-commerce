# EPWO #656 — WorkOwnerIndependentReconciliation.v1, offline source review

Human authorization: 2026-11-10, continuation of #656 after analysis of #607 source audit and #674 hardlink rootfix. Implementation baseline: \`f61435a58a75c00ce486747a8a5447ccb699fda6\`. This is a **source-only reviewer**, not another WorkOwner generation, not a provider attempt and not a native effect settlement.

## Single owner; two different source questions
* **Already implemented and merged (#607):** \`WorkOwnerAudit.run\` authenticates bounded GitHub-source Issue/claim/spawn/journal/candidate facts with a strict GET-only budget, checking replay and races. \`WorkOwnerAuditReport\` always has \`HOLD_UNKNOWN_EFFECT\` even for a source-verified PUBLISHED candidate, because native effect custody and independent signer are separate.
* **New gap addressed in #656:** \`work_owner_independent_reconciliation(report)\` creates a sealed operator **HOLD checklist** from that *existing* audit, with a practical classification, native unknown fields and exact independent proof requirements. \`LocalBatchWorker.independent_reconciliation_action\` acquires exactly one bounded read-only audit (not a WorkOwner claim/append) and projects the plan. \`verify_work_owner_independent_reconciliation\` checks canonical/HOLD packet integrity but is not a signature, signer, third-party provider proof or replay grant.

## Case matrix
| Authenticated source observation | Public classification | Privately unresolved |
|---|---|---|
| #646 root tip \`UNKNOWN\` after \`PROVIDER_INTENT\` | \`UNRESOLVED_PROVIDER_BOUNDARY\` | Child/descendant completion, native EF01 terminal, local diff custody, model effect and billing |
| #698 tip \`PROVIDER_INTENT\` without terminal | \`UNRESOLVED_PROVIDER_BOUNDARY\` | Native provider return, effect and possible descendants; absence of archive is not no-effect |
| Source verifier contradicts GitHub claim, source pin or boundary | \`SOURCE_CONTRADICTION\` | Cannot promote any candidate, owner or external effect |
| Genuine GitHub \`PUBLISHED\` with authenticated candidate source | \`PUBLISHED_SOURCE_ONLY_NATIVE_UNVERIFIED\` | Provider/signer/true effect/independent Windows receipt remain unknown |
| Source cannot be read under bounded GET budget | \`SOURCE_OR_TERMINAL_UNCONFIRMED\` | Must re-establish original source and owner custody before any action |

All modes return \`HOLD_RECONCILIATION_REQUIRED\`. GitHub SHA and a canonical digest do not authenticate native process, external charges, privately enrolled signer or provider effect. Native effect and child tree are **always UNKNOWN** in this source-only consumer. A synthetic/fake audit could be projected as a classification, but no path in this module can turn it into a promotion or privilege.

## Independent recovery evidence still required
1. Read-only independently verified child PID/process tree and descendants have terminated, with a time/owner binding, not only a stale GitHub status or absent PID snapshot.
2. Actual authenticated provider terminal/effect receipt **or** independently attested no-effect finding. A missing local archive and caller-supplied "no effect" are insufficient.
3. Original EF01 terminal, scoped scratch provenance, unpublished local diff/candidate SHA, immutable GitHub WorkOwner fence and idempotency key/cross-issue lineage.
4. Separate enrolled private effect verifier and signer, not the same Windows SID/runner or a GitHub comment.
5. Current main, one new Human-approved action scope, authenticated anti-duplicate and release checks before any future **separately authorized** writable recovery. Do not implement that writer in this source cut.

## Negative AC and release
No same-attempt replay, re-key, edit/delete old WorkOwner journal, owner transfer, provider invocation, prior diff reuse, private Windows filesystem write, PULSE, WakeHub or HostOps effect. Positive and negative source fixtures exercise \`UNKNOWN\`, \`PROVIDER_INTENT\`, native evidence absent, source-verified PUBLISHED still HOLD, forged self-sealed authority, and GET-only non-mutating worker path. Existing EF02 ownership and MT01 owner/consumer tests remain in force.

Source path allowlist: \`src/ai_commerce/remote/{transport,worker}.py\`, \`tests/{test_ef02_m0r_fault_injection,test_ef02}.py\`, this protocol and \`work_items/EPWO_BQ05B1_UNKNOWN_RECONCILIATION_001.json\`. Require Windows exact-head CI, real selected/collected/executed and independent Brain review before automatic merge. **#656 remains OPEN even if this source slice merges** until separate native reconciliation is genuinely demonstrated and acknowledged. The untouched #646/#698 WorkOwner immutable refs remain UNKNOWN/PER_PROVIDER_INTENT; their historic state is not rewritten.
