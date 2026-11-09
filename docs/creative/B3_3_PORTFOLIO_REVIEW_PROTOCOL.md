# B3.3 — A18 Portfolio context × B3.2 attended evidence (source-only)

**Source:** main `5e8503b09f5100a38dfb5ae5e08e3020b9b05790` after B3.2 PR #740 merged; Human continuation #747. Owner: Commerce Opportunity A18 M06. Consumer B3.2 does not become a new authoritative ledger.

## Read-only algorithm
- Read live B3.2 through `review_next_brief`: A09 history and freshness, A11 replay, current A17 pending r1. Stale/correction/reprocess and adopted A17 r2 => HOLD.
- Evaluate A18 through existing `recommend_portfolio` and native typed EvidenceBundle selectors; never trust a caller-retained recommendation as native custody. No recalculation of cost, scoring, FX, revenue or ROI in this bridge.
- Compare normalized **metric digest, cohort digest (non-null), exact before measurement window and timezone**. Incomparable => typed HOLD. Compatible only means **context** matches, not that A18 and A17 refer to the same source ID, order, campaign or measured outcome.
- Output `B3PortfolioReview.v1` with canonical SHA identity, source quality, missingness, reason codes, counterevidence counts, A18 cost totals kept as independent per-currency buckets and original A18 provisional descriptor. All actions, provider attestation, Human adoption, causal lift, ROI, finance and PULSE fields remain null/false/NONE. Public GitHub Issues receive no private source values.

## Trust limits and follow-up
- A18 might return `KEEP`, `CHANGE` or `STOP` under optional Human-provided provisional local threshold policy. These are **advisories only**: not automatic portfolio execution or signed BrainDecision. The bridge labels every context-compatible packet `CONTEXT_COMPATIBLE_JOIN_UNVERIFIED` even with native A18 receipts, until a separate native cross-owner identity join is proven.
- A18 may contain synthetic fixture or user-supplied evidence; retain original `evidence_quality`, `reason_codes`, `missingness`. A09/A11/A17 and A18 stores are not a distributed atomic snapshot, hashes are not signatures or provider receipts. No supplier verification or monetary settlement implied.
- **Brain** decides whether to act on a next brief in a separate attended chat; **Human** owns budget/campaign changes and external provider rights. This is not an automatic PULSE or WakeHub workflow.

## Acceptance gates
Windows exact-HEAD native MT01 group `b3-portfolio`, existing A18.1 and B3.2 consumer regression, negative forged/stale/scope tests, Python/Node hosted CI, independently accepted Brain review and trusted auto-merge only. No local Windows self-host proof is claimed by GitHub source authoring.
