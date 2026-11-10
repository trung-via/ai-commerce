# AI Commerce — Integrated Roadmap Execution Register v2.1

**Audit snapshot:** 2026-11-10 · **source:** `trung-via/ai-commerce@9493bae0664c152df77dc5c7d0b61780f6159f1e` · **scope:** documentation and cross-roadmap evidence, **not** new runtime authorization.

**Machine-readable index:** [ROADMAP_EXECUTION_REGISTER_V2_1.json](ROADMAP_EXECUTION_REGISTER_V2_1.json). This is a **versioned evidence snapshot**, not a self-refreshing source-of-truth service.

## What is fixed — without rewriting historical facts

- Preserve the **63 unique WBS IDs**, original dependency graph, original statuses, six Roadmap A phases and A01–A18 packages. Audit: **0 duplicate IDs, 0 dangling WBS dependencies, 0 cycles**.
- 58 original WBS IDs directly map to A01–A18. The five remaining IDs are **deliberately routed**, not silently lost: `EF01`/`EF02` to Engineering; `C05` to B1.3; `P02`/`P07` to B1.4/B4.1.
- Preserve B-QUALITY-001 as a separate **7-package planning extension** of PA2 Roadmap B, not seven newly renumbered legacy WBS items.
- Historical labels in `WORK_TREE_V1_1.json` and `WORK_TREE_B_QUALITY_001_V1_0.json` reflect **their dated planning snapshot**. In particular `BQ_IMPLEMENTATION_NOT_STARTED` is **not** the current factual status for all BQ packages.
- Do not rewrite settled Human decisions, work ownership, existing WorkItems, source hashes or immutable UNKNOWN outcomes. This overlay is additive and auditable.

## How to read status correctly

| Field | Acceptable evidence | Cannot prove |
|---|---|---|
| Human adoption/approval | Current Human decision and explicit scope | Implementation or effect |
| Source delivery | Exact PR/head/base, actual trusted merge, bounded test/CI evidence | Private Windows deployment, usable media, sales |
| Native Windows action | Owner/session/source-bound native receipt | Browser Send or product provider response unless explicitly observed |
| WakeHub E2E | Exact Chat A origin, current account/route, one authorized Send and UI user-turn witness | Host plugin invocation or downstream Brain dispatch |
| PULSE real provider | Authenticated private authority, rights/entitlement, effect receipt and asset hash | Verified publication, attribution or ROI without their receipts |
| Commerce outcome | Account/window/asset-bound source metrics, attribution and settlement evidence | Causality outside the observed scope |

**Unknown means UNKNOWN, not zero and not complete.** An open Issue can contain accepted merged source; a closed Issue can still carry a historical UNKNOWN provider effect. A green PR CI is not independent Brain PASS or live E2E.

## Snapshot of current evidence and unresolved gates

| Workstream | Most concrete source evidence | Missing proof / risk |
|---|---|---|
| Roadmap A | Historical A01–A11 scoped source; A12 feed-readiness [#758](https://github.com/trung-via/ai-commerce/pull/758); A15/B3.4 [#753](https://github.com/trung-via/ai-commerce/pull/753); A18/B3.3 [#748](https://github.com/trung-via/ai-commerce/pull/748) | Reconcile each A package's **current** scope; A12 source ≠ provider entitlement; A14 ≠ proven commission/settlement |
| PULSE B0–B4 | B0.2 [#323](https://github.com/trung-via/ai-commerce/pull/323), B0.3 [#329](https://github.com/trung-via/ai-commerce/pull/329); B1 audio [#780](https://github.com/trung-via/ai-commerce/pull/780)/[#785](https://github.com/trung-via/ai-commerce/pull/785); B3 [#732](https://github.com/trung-via/ai-commerce/pull/732)/[#748](https://github.com/trung-via/ai-commerce/pull/748) | An integrated offline journey receipt; real provider, assisted publication and authenticated B2 data separately |
| BQ/CRI | BQ-00 [#410](https://github.com/trung-via/ai-commerce/pull/410), BQ-02 [#426](https://github.com/trung-via/ai-commerce/pull/426), BQ-03 [#480](https://github.com/trung-via/ai-commerce/pull/480), BQ-04 [#627](https://github.com/trung-via/ai-commerce/pull/627), BQ-05 [#639](https://github.com/trung-via/ai-commerce/pull/639)/[#768](https://github.com/trung-via/ai-commerce/pull/768), BQ-06 [#732](https://github.com/trung-via/ai-commerce/pull/732) | Actual human study, real provider QA, approved private custody, real behavioral learning |
| WakeHub WH00–09 | WH03 [#734](https://github.com/trung-via/ai-commerce/pull/734), WH04–07 [#775](https://github.com/trung-via/ai-commerce/pull/775)/[#776](https://github.com/trung-via/ai-commerce/pull/776)/[#779](https://github.com/trung-via/ai-commerce/pull/779)/[#783](https://github.com/trung-via/ai-commerce/pull/783) | [WH03 private registration](https://github.com/trung-via/ai-commerce/issues/738), [WH01B logon](https://github.com/trung-via/ai-commerce/issues/499), one genuine exact-chat Send witness; WH08 plugin invocation **not authorized** |

This table intentionally does **not** assign an invented percentage complete. See the JSON `package_executions` array for all **42** active package/lane records and their evidence boundaries.

## Unified release gates — not a new runtime or universal dependency chain

| Gate | Milestone acceptance | Negative acceptance |
|---|---|---|
| G0 — trusted Engineering | Exact-head Windows MT-01 and independently accepted PR | CI red, inherited token guard false-positive or stale owner PASS cannot be promoted |
| G1 — WakeHub Send-only | One source-bound completion routed to the **same registered Chat A**, user turn actually observed | Chat B, wrong account, duplicate/UNKNOWN Send and fabricated plugin tool invocation fail |
| G2 — PULSE/BQ offline journey | Current-source directive → B0 worker/journal → actual offline media QA/repair → WAIT_BRAIN → attended review → evidence-linked next brief | Fixture, synthetic observation, source merge or hypothetical next brief cannot be upgraded to provider/Brain receipts |
| G3 — real creative provider | Separate private rights/provider/account/spend grants and authenticated output SHA/QA | An offline BQ-05 bridge, source digest or UNKNOWN-effect replay cannot grant external execution |
| G4 — distribution/learning | Verified account×surface posting and compatible B2 metrics linked to asset/cohort/window → B3 review | No sales/ROI/growth claims from synthetic, supplied or incomparable data |
| G5 — optional affiliate/business | Human selects the branch, verifies product/placement rights, attribution and settlement where applicable | Affiliate, SKU, own-brand investment is **never** a general creative prerequisite |

### Prioritization and dependency intelligence

**Retired workstream:** EPWO WP01–WP06 was retired by Human on 2026-11-11. Do not dispatch new WP actions or revive PR #784. Independent CI-only source removal must preserve functioning EF01/EF02, MT-01 and non-WP BQ/WH/Production workflows.

**Highest-value missing E2E proofs:** privately enroll WH03 exact Chat A plus WH01B native lifecycle; complete WH07 one-shot Send-only proof. In a separate **parallel** lane, integrate the already merged PULSE/BQ source into one independently observed **offline** journey. Prefer reuse before expanding adapters/fixtures.

**Later external effects:** B0-S custody/rights/entitlement is mandatory *only before its corresponding external effect*. Real provider B1, assisted B2, then evidence-based B3 are independently authorized; B4 is optional. Do not run the commercial critical path as a universal gate for product_ref=null, content-only, silent or fictional creative.

**Anti-issue-roulette:** After two adverse outcomes with one semantic owner, compare the exact failure lineage and repair the common contract before authorizing another attempt. One failed test in a broad dependency chain is not a reason to expand all source allowlists or downgrade verification.

## Reconciliation protocol for subsequent updates

1. Pin fresh `main`, target PR/head/base, actual CI, relevant Issue comments, source owner and any native receipt. Never update `source_state` from a mere PR title or archived candidate.
2. Apply evidence-tier-specific status change. Keep `source_state` and `live_state` **independent**, with refs for both. `CURRENT_REASSESSMENT_REQUIRED` is a data-quality warning, not `NOT_IMPLEMENTED`.
3. Recheck **63/63 unique WBS, zero dangling dependencies, zero cycles, A package mapping coverage and all 7 BQ IDs** before publishing a new version.
4. Preserve immutable historical Git/Issue evidence, including UNKNOWN attempt boundaries; retiring WP does not authorize replay, deletion of history, or mutation of active non-WP workflows. Add a new versioned snapshot for a later `main` instead of rewriting history.
5. For new non-WP Engineering product work, use the separate authorized coding policies. **WP retirement source changes are explicitly CI-first / NO coding executor** under Human 2026-11-11 decision. CI and history inspection do not call PULSE/media providers.
6. Review PR exact HEAD, CI and semantic claims independently. **Do not automatically issue Brain PASS for the new register solely because this document was written.** Trusted merge is a separate verified effect.

## Change scope and rollback

This retirement revision removes EPWO WP01–WP06 from the active execution register and adds source-owned EF02 ingress denial plus a CI regression. No current EF01/EF02 provider invocation, HostOps, Chrome 9444, PULSE, spend, publication or private effect is authorized by this documentation.

Rollback of the staged CI retirement PR does not revive the Human-retired WP program. The remaining WorkOwner runtime migration must have independent Windows CI, Brain acceptance and an actual merged source witness.
