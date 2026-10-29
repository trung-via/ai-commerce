# A08 v1 — Assisted Distribution & Receipts
Date: 2026-10-29. WorkItem: A08-ASSISTED-DISTRIBUTION-001.
Status: VERIFIED_FOR_OFFLINE_SCOPE; merged to main in PR #78.

## Scope
A08 D02/D03 exports a standalone MP4/caption/hashtags/title/manual-instructions package
only after reopening A06 E01 custody and performing its full real-media verification.
The output includes per-file SHA256, source E01 manifest digest, exact target
platform/surface/account, and optional A07 offline advisory decision. Export never
posts, grants rights, accesses provider APIs or changes A06 provenance.

D04 uses SQLite append-only event journal for a manual handoff.
States are distinct: MANUAL_ACTION_REQUIRED, SUBMITTED, UNKNOWN, REJECTED,
PUBLISHED_CONFIRMED and PUBLIC visibility. Recorded PUBLISHED_CONFIRMED requires
user-provided receipt bytes and post ID; PUBLIC requires different evidence bytes
and HTTPS post URL. Evidence is stored as SQLite BLOB with SHA256.
Every record is labeled USER_SUPPLIED_NOT_LIVE_VERIFIED; no live provider truth is claimed.

## CLI
    python -m ai_commerce distribution export --source .local/e01 --output .local/a08-export --platform TIKTOK --surface VIDEO --account internal_alias:creator --caption "Draft"
    python -m ai_commerce distribution inspect --package .local/a08-export
    python -m ai_commerce distribution handoff --package .local/a08-export --db .local/receipts.sqlite --idempotency-key once
    python -m ai_commerce distribution event --db .local/receipts.sqlite --publication-id <id> --type SUBMISSION_REPORTED
    python -m ai_commerce distribution status --db .local/receipts.sqlite --publication-id <id>

## Limits and safety
A08 supports no real provider posting, no account entitlement assertion, no live
confirmation, no product link proof or audience measurement. A07 ALLOW remains
OFFLINE_RECORDED_NOT_LIVE_VERIFIED and is never used to authorize external side
effects. The journal intentionally does not implement D05 distributed publisher
reconciliation. The draft PublicationRecord.v0.2 remains the semantic baseline.

## Verification
Observed GitHub evidence on A08 candidate `8fa97aae0c9a2e6584e4672790628d6d4681c5f6`:
- [A08 CI #40831199268](https://github.com/trung-via/ai-commerce/actions/runs/40831199268): **60 passed Windows; 60 passed Ubuntu** (affected tests including A06/A07 and CLI).
- [A07 CI #40831199192](https://github.com/trung-via/ai-commerce/actions/runs/40831199192): **65 passed Windows; 65 passed Ubuntu**.
- [A06 full regression #40831199309](https://github.com/trung-via/ai-commerce/actions/runs/40831199309): **907 passed Windows; 906 passed, 1 skipped Ubuntu**, including real MP4 demo and full decode workflow.
- [PR #78](https://github.com/trung-via/ai-commerce/pull/78) merged into `main@8d02bd15613220c06bcfb67225622674d70b3ffc`.

**Acceptance:** D02/D03/D04 technical OFFLINE scope SATISFIED; M2 OFFLINE_ASSISTED_SCOPE accepted. This verifies only locally prepared packages, manual handoff and a journal able to record USER_SUPPLIED_NOT_LIVE_VERIFIED evidence. It does **not** verify actual platform account entitlements, a successful real posting, public visibility or real commerce metrics. D05–D08 and A09 remain separate and unaccepted.
