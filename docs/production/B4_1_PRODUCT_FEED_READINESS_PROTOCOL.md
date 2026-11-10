# B4.1a — Offline product-feed readiness / A12 single owner

**Roadmap B4 optional.** Exact source `a7797b06bc8e99e23c7dd22fbeaf5ffaac598633`, Issue #757. This is only the **offline A12 source review slice**, not B4.1 authorized provider feed collection. A Human selecting an actual provider/API/marketplace does not follow from approving an offline roadmap step.

## Read-only implementation

`B4FeedReadiness.v1` reopens native A12 `ProductEvidenceRegistry` and validates its caller-pinned local `head_hash`; regenerates authentic `ProductEvidencePack` at an explicitly requested observation instant/age; repeats the native integrity read and checks event refs. Native A12 keeps all product identity, seller/variant, offer freshness/price/conflicts, claims and asset-rights data. The new bridge stores **no** second product ledger or raw seller data; it exposes only bounded hashes, IDs, statuses and reason codes.

An A12 `COUNTEREVIDENCE` event relevant to the selected pack is an **affected evidence review trigger**; it is NOT verified external provider revocation, actual rights rescission or permission to rewrite any downstream creative asset. Expired locally reviewed rights are flagged using existing exact term timestamps; the bridge does not grant a use-specific commercial/ad/derivative license. A local A12 offer marked `VALID_OBSERVATION` is still source-asserted, not the current API price; no affiliate account eligibility or commissions are confirmed.

## Denial and trust
- Missing source identity, wrong pinned head, damaged native A12 registry, unavailable variant, changed concurrent read => HOLD, no implicit latest-wins or alternate feed.
- Synthetic source, missing/unknown/restricted/mixed rights, stale/expired/conflicting/no offer, source capture outside window and counterevidence => separate typed review reasons, never false success.
- Hash chain is not signed provider evidence; checking head twice reduces obvious races but is not an atomic cross-account transaction or anti-rollback against a replaced registry **and** caller pin.
- `OFFLINE_ADVISORY_ONLY` means local A12 context may be reviewed, not permission for live source extraction, scraping, publication, spend or PULSE; `LOCAL_EVIDENCE_HOLD` means additional local review required.

## Separate gated future work
A real P02 provider-specific feed/API adapter requires Human-chosen provider/region, verified TOS/entitlement/account, independent private connector custody, retrieval timestamps and verifiable revocation mapping; only then can native A12 import new claims/offer revisions via authorized owner. This source PR cannot exercise or simulate that entitlement. No Codex/Antigravity provider executor, Chrome 9444, HostOps, PULSE or live effects. Windows MT01 and independent Brain PASS required before source merge; this is not a B4.1 live completion claim.
