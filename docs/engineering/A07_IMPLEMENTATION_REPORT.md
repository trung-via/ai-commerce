# A07 v1 — Implementation Report
Date: 2026-10-29. WorkItem: A07-CHANNEL-CAPABILITY-001.
Status: IMPLEMENTED_PENDING_HOSTED_CI.

## Scope and provenance
Provider-neutral offline metadata capability registry (D01) with immutable SQLite history, strict JSON Schema Draft 2020-12, exact (platform/account/app/market/surface/action) matching, expiration and revocation, evidence kinds and digests, platform profile version and deterministic evaluator.
A08 consumes `ChannelCapabilityDecision.v1`: `ALLOW | DENY | UNKNOWN | NEEDS_RECHECK`. This decision is **OFFLINE_RECORDED_NOT_LIVE_VERIFIED**, not proof of currently valid provider credential, entitlement or publication. `simulation=true` can evaluate TEST_FIXTURE but must not authorize external actions.

## Interfaces
```bash
ai-commerce channel-capability validate --file snapshot.json
ai-commerce channel-capability import --file snapshot.json --db .local/capabilities.db
ai-commerce channel-capability evaluate --request request.json --db .local/capabilities.db
ai-commerce channel-capability history --request request.json --db .local/capabilities.db
```
Evaluate return codes: 0 ALLOW, 10 UNKNOWN, 20 NEEDS_RECHECK, 30 DENY, 40 invalid input, 50 database error. Missing provider evidence never proves ALLOW, and publication receipts remain A08.

## No-effects boundary
A07 never contacts social APIs, stores credentials, publishes, spends or imports unrelated project authorization. The four provider profiles encode documentation metadata and illustrative scope names only. No verified TikTok/Facebook/Instagram/YouTube account is claimed.

## Verification
CI pending for source branch. Hosted test results and cross-platform evidence must be added only after actually observed.

## Sources
- TikTok upload: https://developers.tiktok.com/docs/en/content-posting-api-reference-upload-video
- TikTok direct post: https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post
- Meta Facebook collection: https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api
- Meta Instagram collection: https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api
- YouTube videos.insert: https://developers.google.com/youtube/data/reference/rest/v3/videos/insert

## A08 boundary
Do not treat A07 recorded ALLOW as permission to run actual external publishing without independent current rights and account/API revalidation. Keep SUBMITTED, PUBLISHED_CONFIRMED, PUBLIC and product-link evidence distinct.
