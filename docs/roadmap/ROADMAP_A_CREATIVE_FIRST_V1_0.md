# AI Commerce — Roadmap A: Creative-First v1.0

**Canonical planning · Human-approved 2026-10-28 · No dispatch / implementation implied.**

Source: Work Tree v1.1 (63 WBS IDs); prior Roadmap A six phases/A01–A18 selected by Human. Governance v0.3 prevails. Work package boundaries are adjustable by Human and are not independent AIOS TASKs. This is the first repository-canonical Roadmap A artifact; phase names and IDs follow the earlier selected plan, while this file records detailed package mapping and Phase-0 EF decisions.

**Engineering Flow is not a Commerce Product feature.** EF executes **engineering code changes**; product operations O01–O07 orchestrate **media/business jobs**. The repo stays independent and is not downstream of AIOS.

## Six stages and work packages

### Phase 0 — Engineering Foundation

Milestone(s): `M0`, `M0-R`.

| Package | WBS mapped | Depends on | Deliverable and observable acceptance |
|---|---|---|---|
| `A01` **Project Bootstrap** | `G01`, `G02`, `G03`, `G06`, `G08` | — | Independent repository skeleton, conventions, targeted tests and isolation. Clean local CLI/test foundation; compatible with separately approved selective AIOS code adoption. |
| `A02` **Contracts & Product Adapters** | `G04`, `G05`, `G07` | `A01` | Typed contracts, evidence identity, mock product/provider adapters. Schemas validate valid/invalid data; mock produces traceable output. |

**Phase-0 engineering lane (two WBS items beyond A01/A02):** `A01/G03,G06,G08 → EF01`; `A02` can proceed in parallel; `EF01 → EF02` when remote wake is developed. `EF02` is **not** a blocking dependency for Phase 1 offline creative.

| EF ID | Scope | Targeted acceptance |
|---|---|---|
| `EF01` (P0) | Selective AIOS code extraction to **independent** AI Commerce; per-WorkItem **Codex or Antigravity**, manual model/effort choice, optional advisory recommendation, adapter + focused tests + diagnostic + Git candidate + usage observation. | One bounded real code change **per executor**, result normalized and verified; exact source and model attributed; no imported runtime or inherited hard default. |
| `EF02` (P0) | **Multi-Action v1**: Human/Brain concurrent batch ingress, DAG + two overlapping Windows EF01 processes initially (configurable), per-attempt claim/worktrees/candidate and per-action/aggregate GitHub feedback; CLI repair attempt new; optional Chat Wake/native independently proven. | 5-action batch with at least 2 truly concurrent independent executor processes, no duplicate invocation, correct dependency and failure evidence, Brain-readable aggregate; EF02 not gate for Creative Factory. |


### Optional EF enhancements after accepted M0-R (parallel lane, no phase gate)

Human accepted EF01/EF02 extension planning on 2026-10-28; `M0-R = ACCEPTED_WITH_DEFERRED_RISKS`, actual Windows runner-service outage remains deferred and UNKNOWN. The six phases, A01–A18 mapping and Creative Factory dependencies **do not change**.

| Order | EF work item | Phase placement | Non-blocking delivery |
|---|---|---|---|
| 1 / P1 | [EF01-R Verify–Repair](../../work_items/EF01-VERIFY-REPAIR-002.json) | Phase 1, parallel with A03/A04 | Same EF02 action; bounded in-action repair only after determinate verification failure; original executor/model/effort and no-blind-replay preserved. |
| 2 / P1 | [EF02-D Exact-chat Brain Wake](../../work_items/EF02-EXACT-CHAT-WAKE-002.json) | Phase 1, independent of EF01-R | Wake only with verified same-conversation delivery; otherwise GitHub feedback is fallback. |
| 3 / P2 | [EF01-P Direct Git & 12 pytest workers](../../work_items/EF01-DIRECT-GIT-PYTEST-003.json) | Phase 1 or 2 after real Windows measurement | Optional git executable and xdist profile; 12 pytest workers != 12 model executors and two concurrent EF02 actions must honor host capacity. |

Source decision: [Human EF enhancement roadmap](../governance/HUMAN_DECISION_2026-10-28_EF_ENHANCEMENTS.md). Each is **PLANNED/NOT IMPLEMENTED**, requires separate verified code delivery when prioritized; not new M0-R criteria.

### Phase 1 — Creative Factory

Milestone(s): `M1`.

| Package | WBS mapped | Depends on | Deliverable and observable acceptance |
|---|---|---|---|
| `A03` **Freeform Brief & Creative Reasoner** | `C01`, `C02` | `A02` | Editable content briefs and creative concept generation. Concept generation without required SKU, quota or mandatory storyboard. |
| `A04` **Media Sources & Local Operations** | `O01`, `O02`, `O03`, `O04`, `C04` | `A02` | Local asset job/runner and media adapter. One mock or selected asset run with traceable logs; unknown remains unknown. |
| `A05` **Script, Scene & Video Render** | `C03`, `C06` | `A03`, `A04` | Prompt/brief to reproducible video render. Playable MP4 with manifest and diagnostics. |
| `A06` **Offline Creative E2E** | `E01` | `A05` | First end-to-end creative artifact. Usable video with traceable generation; no publish/account proof required. |

### Phase 2 — Distribution, Audience & Opportunity

Milestone(s): `M2`, `M3`.

| Package | WBS mapped | Depends on | Deliverable and observable acceptance |
|---|---|---|---|
| `A07` **Channel Capability Foundation** | `D01` | `A02` | Account×surface capability truth registry. Permission UNKNOWN separate from allowed; no shared AIOS accounts. |
| `A08` **Assisted Distribution & Receipts** | `D02`, `D03`, `D04` | `A06`, `A07` | Export package, assisted posting path, publication record. SUBMITTED, confirmed and PUBLIC states distinct; no fictional provider receipt. |
| `A09` **Audience Growth Observation** | `L01`, `L02`, `E02` | `A08` | Audience loop with export/import path. Account-specific metrics imported when real; offline PARTIAL is honest. |
| `A10` **Opportunity & Audience Intelligence** | `M01`, `M02`, `M03`, `M04`, `M05` | `A03` | Trend, audience and opportunity hypotheses into Creative. Snapshot distinguished from trend; content-only ideas possible. |

### Phase 3 — Learning & Affiliate Commerce

Milestone(s): `M4`.

| Package | WBS mapped | Depends on | Deliverable and observable acceptance |
|---|---|---|---|
| `A11` **Audience Learning Registry** | `L03`, `L06` | `A09` | Observations and hypothesis learning. Metric window/source/version retained; correlation not asserted as causation. |
| `A12` **Product Evidence Foundation** | `P01`, `P03`, `P04`, `P05`, `P06` | `A02` | Product facts/variants/asset-rights pack. Seller claims, verified facts and UNKNOWN rights separate. |
| `A13` **Affiliate Release Evidence** | `C08`, `C09`, `D06`, `E03` | `A12`, `A07`, `A08` | Claim/rights and account×link eligibility release pack. Unknown eligibility blocks only actual commercial placement, not creative production. |
| `A14` **Affiliate Commerce E2E** | `L04`, `E04` | `A13`, `A06` | Affiliate content loop with attribution status. Publish/link/order/settlement distinct; no fabricated revenue. |

### Phase 4 — Commerce Operations & Economics

Milestone(s): `M5`.

| Package | WBS mapped | Depends on | Deliverable and observable acceptance |
|---|---|---|---|
| `A15` **Operational Cost & Idempotency** | `O05`, `O06`, `O07` | `A02` | Cost ledger, dedupe/reconciliation and operational status. Estimated vs charged separate; retry does not blindly duplicate side effects. |
| `A16` **Settlement & Commercial Evidence** | `L05`, `E05` | `A14`, `A15` | Settlement-aware cost and commercial review. Order created/refunded/settled separated; attribution scope explicit. |

### Phase 5 — Scaling & Own Brand

Milestone(s): `M6`.

| Package | WBS mapped | Depends on | Deliverable and observable acceptance |
|---|---|---|---|
| `A17` **Creative & Channel Scaling Extensions** | `C07`, `C10`, `D05`, `D07`, `D08`, `L07`, `E06` | `A11`, `A15` | Variants, feedback, expanded distribution and dashboards. No mandatory scale; external posting only with actual access and authorization. |
| `A18` **Portfolio & Own Brand Decision** | `M06`, `M07`, `E07` | `A10`, `A11`, `A15`, `A16` | Decision support for scale and own-brand option. Human controls inventory, capital and authorization; no automatic commitment. |

## Milestones & evidence

| Milestone | Meaning |
|---|---|
| `M0` | A01 and A02 planning scope plus EF01 core implemented with independent dual-executor capability; EF02 remote is tracked separately as M0-R rather than a gate for Creative Factory. |
| `M0-R` | EF02 batch remote dispatch proven: at least two real overlapping Windows EF01 actions, DAG-aware results and GitHub feedback. Proactive Chat Wake/native multi-turn proof is separate. |
| `M1` | E01 playable offline video and artifact provenance. |
| `M2` | Assisted distribution package and truthful status receipts. |
| `M3` | Audience measurement/insight prototypes, not fabricated live outcomes. |
| `M4` | Audience learning and affiliate release/E2E evidence recorded at actual entitlement level. |
| `M5` | Scoped costs, attribution and settled economics evidence where available. |
| `M6` | Optional scaling and own-brand decision support; Human retains final authority. |

## Critical dependency paths (planning, not universal gates)

- Creative: `A01 → A02 → A03/A04 → A05 → A06 (E01)`. Where Human chooses, offline creative can start in parallel with EF02 and use a manual engineering route if needed.
- Audience: `A06 → A08 → A09 → A11`, with `A07` providing channel capability evidence and `A10` feeding optional opportunity intelligence.
- Affiliate: `A12 + A07 + A08 → A13 → A14`; release rights/eligibility apply at the actual publishing/linking action, not as global barriers to creative rendering.
- Economics: `A14 + A15 → A16`.
- Scale/brand: `A10 + A11 + A15 + A16 → A18`, and `A17` is a selectable extension rather than a universal prerequisite.
- Engineering: `A01 (G03,G06,G08) → EF01 → EF02`; `M0-R` tracks remote readiness; EF02 does not gate `M1`.

## EF-01 engineering execution choices

- **MANUAL**: Human sets executor, exact model and reasoning effort for that WorkItem. Codex and Antigravity are both in initial EF01 scope. Persist chosen selection and do not silently change it mid-attempt.
- **RECOMMEND**: Brain may suggest a provider based on current evidence and observed usage; it is advisory only. No auto-switch unless separately authorized.
- **AUTO**: not selected/activated by this planning decision; quotas, actual model availability and cost require account/tool evidence rather than guesses.
- **Token accounting:** provider-returned counters only; missing token/quota/cost data remain UNKNOWN. Cross-provider raw token counts are not treated as equal prices.
- **Selective extraction:** pin source `trung-via/AIOS-renew@cbefb92612f24b55417165736c63753fc560d403`; explicitly review component provenance, rights/license, dependencies, security/compatibility and rollback before copying; no AIOS workflows/secrets/lifecycle copied implicitly.
- **Execution result:** source SHA, WorkItem + attempt identity, selected executor/profile, changed files, test command/outcome, failure diagnostics, Git candidate/reference. Code verified for scope does not mean live product or publication.

## Activation status

- **Accepted:** Roadmap A plan, EF01 dual executor, selective extraction principle, EF02 independent remote/feedback design.
- **Not performed:** code transplant, GitHub Actions enablement, installing/configuring provider CLIs, Codex/Antigravity runs, tests, publishing, spending, or automatic next-task dispatch.
- **First suggested execution cut:** A01 foundation minimal → EF01 dual-executor core and A02 contracts in parallel; creative A03/A04 need not wait for EF02. Human can change order/scope without mandatory gates.

[Work Tree v1.1](WORK_TREE_DETAILED_V1_1.md) · [Machine-readable Roadmap A](ROADMAP_A_CREATIVE_FIRST_V1_0.json) · [Human decision](../governance/HUMAN_DECISION_2026-10-28_ENGINEERING_FLOW.md)

## Implementation progress (2026-10-28)

| Package | Observed status | Evidence |
|---|---|---|
| `A01` | **BUILD_VERIFIED_FOR_SCOPE** — Python bootstrap and 11 focused tests on local Linux runner | [A01 report](../engineering/A01_IMPLEMENTATION_REPORT.md) |
| `A02` | NOT_STARTED | None |
| `EF01` | NOT_IMPLEMENTED | None |
| `EF02` | NOT_IMPLEMENTED | None |
| `M0` / `M0-R` | NOT_ACHIEVED | A02 and both EF lanes outstanding |

This status does not claim installed-native Codex/Antigravity, CI, Windows conformance or Creative Factory execution. WorkItem [A01-BOOTSTRAP-001](../../work_items/A01-BOOTSTRAP-001.json) records scope and evidence.

## A02 delivery observation — 2026-10-28

| Work package | Current status | Evidence |
|---|---|---|
| `A02` | `BUILD_VERIFIED_FOR_SCOPE` locally; 31 project tests PASS; Windows test not run | [Implementation report](../engineering/A02_IMPLEMENTATION_REPORT.md) |
| `EF01` | NOT_IMPLEMENTED | No executor bridge |
| `EF02` | NOT_IMPLEMENTED | No remote wake |
| `M0` | NOT_ACHIEVED | EF01 pending |

A02 completed G04/G05/G07 with portable product contracts and mock adapter only; no real creative media or third-party API call was performed.

## A02 Windows regression follow-up (2026-10-28)

Human Windows screenshot for previous A02 revision: 30 passed / 1 failed (timestamp format did not raise); mock generate succeeded. The package now registers its own strict date-time checker, removing reliance on optional JSON Schema format checker availability. After patch: **41 local tests passed**, 5 schemas still packaged. **Windows post-fix result is pending**. A02 remains `BUILD_VERIFIED_FOR_SCOPE` for its locally verified implementation; it has **not** achieved a Windows PASS on the new revision. EF01/EF02 remain NOT_IMPLEMENTED and M0 remains NOT_ACHIEVED.

[Regression evidence](../engineering/A02_IMPLEMENTATION_REPORT.md#windows-validation-issue-and-cross-platform-fix-2026-10-28).

## A02 Windows verified — 2026-10-28

Following the cross-platform date-time validator fix, Human supplied a Windows PowerShell screenshot of a successful editable install and **41 targeted tests PASS in 2.08s**, no failures shown. This replaces the historical RETEST_PENDING status for A02; the prior 30/1 failure remains recorded for provenance. No exact Windows checkout SHA was displayed. A02 remains `BUILD_VERIFIED_FOR_SCOPE`; EF01/EF02 remain NOT_IMPLEMENTED and M0 is NOT_ACHIEVED.

[Windows evidence transcription](../engineering/A02_IMPLEMENTATION_REPORT.md#windows-retest-pass-2026-10-28-human-provided-screenshot).

## EF01 implementation evidence update — 2026-10-28

Prior EF01 `NOT_IMPLEMENTED` snapshots in this roadmap are historical. EF01 local dual-executor code was committed on `ef01-dual-executor-2026-10-28` and passed 12/12 synthetic subprocess/Git targeted tests. Real Codex + Antigravity Windows code-change acceptance remains **NOT_VERIFIED**, so the M0 live engineering milestone is not asserted complete. EF02 remote remains unimplemented. Details: `docs/engineering/EF01_IMPLEMENTATION_REPORT.md`.

## EF02 Multi-Action approval supersession — 2026-10-28

Human approved **EF02 Multi-Action v1** for implementation. The older EF02 single-remote-invocation acceptance sentence and historical EF01 NOT_IMPLEMENTED notes above no longer describe current acceptance truth. M0 Core is verified for scope (EF01 two live executors; 53/53 Windows tests), while **M0-R remains OPEN** until remote execution proves **at least two simultaneous independent EF01 processes** inside one batch. Default process concurrency 2 is adjustable; many actions, dependencies and concurrent inbound batches are admitted with explicit status (single-runner cross-batch processing may queue). Per-action results, distributed claims, Git candidate refs and aggregated feedback are part of EF02 core. Native proactive Chat Wake is optional, not an implied feature. See [Human decision](../governance/HUMAN_DECISION_2026-10-28_EF02_MULTI_ACTION.md), [implementation handoff](../engineering/EF02_MULTI_ACTION_IMPLEMENTATION_PLAN_V1_0_2026-10-28.md), [WorkItem](../../work_items/EF02-MULTI-ACTION-001.json).

## 2026-10-29 — Human approves A03/A04 v1.1 implementation and finalizes A05

The earlier A05 research-only draft is now **ACCEPTED_FOR_WORK** for a bounded implementation: C03 Composition IR (freeform input, immutable editable revisions, optional scenes/tracks) + C06 FFmpeg/ffprobe local renderer and real playable MP4 manifest/diagnostics. A03/A04 v1.0 remain independently accepted from PR #55; backward-compatible v1.1 capability upgrade is newly authorized. A06 E01 and M1 remain NOT_VERIFIED until a real usable creative E2E is observed. No mandatory storyboard, SKU, CTA, 9:16, live model, external publish or paid media account is implied. See [Human decision](../governance/HUMAN_DECISION_2026-10-29_CREATIVE_V1_1_A05.md), [A03/A04 handoff](../engineering/A03_A04_V1_1_ENGINEERING_HANDOFF.md), and [A05 handoff](../engineering/A05_IMPLEMENTATION_HANDOFF_V1_0.md). The earlier planning statuses in the table above are historic snapshots, superseded for A03/A04 upgrade and A05 by this decision.
