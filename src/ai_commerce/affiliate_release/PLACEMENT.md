# D06 affiliate placement offline evidence

`evaluate_affiliate_placement` and `review_affiliate_placement` evaluate offline
affiliate placement evidence across 7 dimensions:

```
account x app x market x surface x product x variant x link
```

It performs strict offline evaluation of:
- 7 dimensions of affiliate placement
- Evidence provenance & tamper detection
- Required commercial affiliate disclosure (statement and recognized tags)
- Expiry and revocation terms
- Hostile URL detection (SSRF, cloud metadata 169.254.169.254, loopback, private networks, malicious schemes, credentials in URL, suspicious ports, injection patterns)
- Cross-account, cross-app, cross-market, cross-surface, cross-product, and cross-variant mismatches

## Invariants and Guarantees

1. **Parsed URL alone MUST NOT imply active eligible link:**
   - Syntactically valid URLs are evaluated for safety only.
   - Live link verification is never performed.
   - Receipts explicitly state `live_link_verified=False`, `account_eligibility=UNKNOWN`, `link_activation=UNKNOWN`, `platform_approval=UNKNOWN`, and `publication_status=NOT_ATTEMPTED`.
2. **Decisions HOLD / UNKNOWN / REVIEW only:**
   - Decisions can NEVER be `APPROVED`, `ACTIVE`, `ELIGIBLE`, or `POSTED`.
   - `HOLD`: Safety issue, hostile URL, cross-account mismatch, expired/revoked, missing disclosure, tampered hash.
   - `UNKNOWN`: Incomplete evidence, unproven provenance, unknown capability state.
   - `REVIEW`: Advisory offline evidence replayed and consistent; requires manual human review.
3. **No OAuth, live provider calls, posting, real approvals, sales or earnings claims:**
   - Credential material (access tokens, client secrets, bearer tokens) is strictly rejected with `HOLD`.
   - `sales_claims_allowed=False`, `earnings_claims_allowed=False`, and `sales_earnings_claims=NOT_PERMITTED`.
4. **Self-contained Draft 2020-12 Schema:**
   - Schema defined in `affiliate_placement_schema()` and mirrored at `contracts/AffiliatePlacementEvidence.v1.schema.json`.
5. **Deterministic Replay and Immutability:**
   - Canonical UTF-8 JSON serialization and SHA-256 record sealing.
   - Restoration via `AffiliatePlacementEvidence.from_bytes` verifies hash integrity and recomputes all checks.
