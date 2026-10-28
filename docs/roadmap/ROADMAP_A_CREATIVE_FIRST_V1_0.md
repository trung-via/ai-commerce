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
| `EF02` (P0) | GitHub/Windows remote wake, attempt-bound feedback, authorized CLI repair, optional true native multi-turn after live conformance. | One remote invocation returns verifiable result; missing session/status is UNKNOWN/FAILED, never treated as pass. |


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
| `M0-R` | EF02 remote execution and feedback independently verified when built; optional native multi-turn proof reported separately. |
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
