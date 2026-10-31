# E03 Affiliate Release Evidence Pack

`AffiliateReleaseEvidencePack.v1` (`build_affiliate_release_pack`,
`evaluate_affiliate_release_pack`, `review_affiliate_release_pack`) is an
offline-only, advisory manual-review artifact integrating:

- **C08** media technical inspection (`MediaReleaseInspection.v1`)
- **C09** claim and asset rights release review (`ClaimRightsReleaseReview.v1`)
- **D06** affiliate placement eligibility (`AffiliatePlacementEvidence.v1`)
- **A12** product/variant snapshot, identity, claim, offer, and rights evidence (`ProductEvidencePack.v1`)
- **A07** channel capability decision/snapshot (`ChannelCapabilityDecision.v1` / `ChannelCapabilitySnapshot.v1`)
- **A08** manual distribution handoff package (`DistributionPackage.v1`, optional)

## Revalidation & State Machine

`AffiliateReleaseEvidencePack` revalidates:
1. **Scope consistency** across `platform`, `account_ref`, `app_ref`, `market`, `territory`, `surface`, `channel`, `product_id`, `listing_id`, `variant_id`, `release_sha256`, and `link_url`.
2. **Commercial affiliate disclosure** (clear and conspicuous notice + recognized affiliate tags, plus consistency with D06 and A08).
3. **Active date, expiry, and revocation** across the release window (`as_of`, `valid_from`, `use_from`, `use_until`, `valid_until`, `revoked_at`), A12 offer expiry/staleness, C09 license term and status notices, D06 placement window, and A07 capability expiry/revocation.
4. **Hostile URL and tampered hash detection** (SSRF, loopback, private networks, cloud metadata `169.254.169.254`, dangerous schemes, credentials, path traversal, script injection, resealed or modified digests).
5. **Missing or conflicting evidence** (missing upstream evidence yields `UNKNOWN`; conflicting claims/offers or restricted/revoked/failed components yield `HOLD`).

## Advisory-Only Invariants

- Outputs are strictly `READY_FOR_MANUAL_REVIEW`, `HOLD`, or `UNKNOWN`.
- Never outputs `APPROVED`, `ACTIVE`, `ELIGIBLE`, `POSTED`, or `PUBLIC`.
- Every receipt enforces:
  - `advisory_only=True`, `manual_review_required=True`
  - `live_provider_approval="UNKNOWN"`, `live_link_verified=False`, `affiliate_link_status="UNKNOWN_OFFLINE_ONLY"`
  - `account_eligibility="UNKNOWN"`, `link_activation="UNKNOWN"`, `platform_approval="UNKNOWN"`, `legal_rights_status="UNKNOWN"`
  - `publication_status="NOT_ATTEMPTED"`, `auto_publishing="NEVER_PERFORMED"`
  - `oauth_performed=False`, `network_calls_performed=False`
  - `sales_claims_allowed=False`, `earnings_claims_allowed=False`, `sales_earnings_claims="NOT_PERMITTED"`, `revenue_status="NOT_CLAIMED"`

## CLI & Deterministic Demo

```bash
python -m ai_commerce.affiliate_release schema --contract pack
python -m ai_commerce.affiliate_release demo --output <new_temp_dir>
python -m ai_commerce.affiliate_release verify --file <pack.json> --expected-id <a13-pack-...>
```
