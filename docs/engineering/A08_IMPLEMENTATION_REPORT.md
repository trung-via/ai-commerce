# A08 v1 — Assisted Distribution & Receipts
Date: 2026-10-29. WorkItem: A08-ASSISTED-DISTRIBUTION-001.
Status: IMPLEMENTED_PENDING_HOSTED_CI.

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
Pending GitHub Actions on Windows and Ubuntu; do not claim acceptance before CI.
