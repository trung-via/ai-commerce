# A07 v1 — Implementation Report
Date: 2026-10-29. WorkItem: A07-CHANNEL-CAPABILITY-001.
Status: VERIFIED_FOR_OFFLINE_SCOPE; MERGED_MAIN 0e57caf9d23b0aae01627490a42f3de2068f95c8.

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
GitHub PR [#75](https://github.com/trung-via/ai-commerce/pull/75) merged using squash commit `0e57caf9d23b0aae01627490a42f3de2068f95c8` on 2026-10-29.
Source candidate `d991f747752b05bbf30769ece5924a3acb46bc4c` passed:
- [A07 CI #40824806875](https://github.com/trung-via/ai-commerce/actions/runs/40824806875): **65 tests passed on Windows and 65 tests passed on Ubuntu**.
- [A06 regression #40824806922](https://github.com/trung-via/ai-commerce/actions/runs/40824806922): **897 passed on Windows; 896 passed + 1 skipped on Ubuntu**.
The two CI workflows used the same PR candidate SHA. These results verify recorded/offline behavior and compatibility, not live platform entitlements or actual distribution. A08 and M2 remain PLANNED / NOT_VERIFIED.

## Sources
- TikTok upload: https://developers.tiktok.com/docs/en/content-posting-api-reference-upload-video
- TikTok direct post: https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post
- Meta Facebook collection: https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api
- Meta Instagram collection: https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api
- YouTube videos.insert: https://developers.google.com/youtube/data/reference/rest/v3/videos/insert

## A08 boundary
Do not treat A07 recorded ALLOW as permission to run actual external publishing without independent current rights and account/API revalidation. Keep SUBMITTED, PUBLISHED_CONFIRMED, PUBLIC and product-link evidence distinct.

## Offline evidence confidence limitation
A07 stores supplied hash/metadata for redacted evidence, and protects SQLite snapshot payload integrity. It does not read, cryptographically verify, or live-confirm third-party account evidence; a user-entered SHA is metadata rather than proof of platform entitlement. A08 must revalidate external rights before any real action. Provider supported surfaces and action scopes are metadata, not entitlements. A08 decision JSON is validated against a versioned Schema 2020-12 contract.
