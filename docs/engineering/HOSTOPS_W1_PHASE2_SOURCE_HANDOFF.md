# HostOps W1.1–W1.2 — offline intent and external credential custody (source-only)

Parent: #309 and one-account #293. Base source: `5e7d8eacaa7b6cfc845ca8d32e59de705cd325f2`, merged PR #312.

## Scope and observed state

R1 attended and R2 supervised fixed remote reads have live evidence. W1 #312 verifies a real signed OpenSSH/Ed25519 create-new proposal only. This follow-up adds an **offline** SQLite intent prototype and a conservative custody-claim classifier; it does not add a W1 CLI command, GitHub issue polling operation, executor, Windows service, file-creation adapter, credential persistence, host permission enrollment, or browser Wake. None of the W1 functions may be construed as a release gate for active file writes.

## W1.1 — credential/signature custody decision

Exactly one interactive Windows account, no new local or service accounts. A public GitHub Actions self-hosted runner under the same Windows SID can read same-user files and sometimes process material. DPAPI, NTFS ACLs, Credential Manager, HMAC secrets, obscured task scheduling, and `hidden` files are **not** an independent privilege boundary.

Review two modes:

- **Human-attended**: fine-grained temporary private-control credentials supplied and removed in a supervised session, never retained for an always-on service. Short-lived credentials still need explicit disclosure/expiration bounds. This is the only currently observed read pattern, not a verified write.
- **Externally isolated broker**: independently operated signer and token issuer holding their private keys outside the shared public-runner Windows token, issuing a time-limited, audience-scoped authorization per operation. GitHub App installation tokens may provide short-lived repository access but a stolen live token remains usable until expiration/revocation. Its minting private key must never reside on the same-SID laptop. Architecture remains `HOLD_EXTERNAL_ATTESTATION_REQUIRED` until independently demonstrated provider identity, access boundaries, revocation and theft tests.

`classify_custody_claim` is intentionally NOT an attestor: even claims that external mechanisms exist cannot make `unattended_ready` or `write_enabled` true. The trust root must be verifiable independently of any file, PID, user token or JSON written by a public-runner job.

## W1.2 — offline signed intent, not a write executor

`src/ai_commerce/hostops_v3/w1_offline_journal.py` exposes `OfflineW1IntentLedger`. It calls the already-reviewed `personal_w1_verify_offline` **before** reserving the request. It stores request ID, unsigned signed-payload SHA256, private Issue number and pinned source SHA in an SQLite database supplied by the test/reviewer; never raw file content, PAT, key or signature. It sets SQLite `synchronous=FULL` and records an `UNKNOWN_UNRECONCILED` intent in a transaction.

- A duplicate request ID with the same exact bytes returns `HOLD_REPLAY_UNKNOWN`, not permission to execute.
- A request ID reused with different content/target/Issue/source returns `W1_REQUEST_ID_CONFLICT`.
- Invalid/expired/wrong-source/forged requests are rejected before recording.
- Crash or loss of a receipt leaves the original intent unresolved. No automatic retry, no transition to `APPLIED`, no user-file effect, and no terminal/CLI dispatch path.
- This is an **offline ledger prototype only**. It is not protected against malicious same-SID modification, rollback or database replacement; durable SQLite alone does not prove independent trust custody or power-loss recoverability on arbitrary storage.

## Further requirements for any W1 native create-new implementation

1. Human explicitly approves the exact real operation, scratch root and content. No arbitrary filesystem roots, no overwrite, no shell or sensitive read.
2. Independently verify external signer identity, public policy hashes and bounded per-operation signed authorization. Bind source SHA, private Issue, request ID, filename, content bytes and SHA256, expiration and recipient.
3. Protect the journal and its monotonic anti-rollback anchor from same-SID public-runner compromise; fail closed on missing/rollback journal and ambiguous effect. No retry of UNKNOWN.
4. On Windows, use native handle-based create-new with no-follow/reparse controls, safe directory root resolution, no overwrite and read-back SHA. Avoid TOCTOU by proving handle binding rather than prechecking path text alone.
5. An isolated Windows self-host native test and an independently inspected disk receipt are necessary before claiming `SIGNED_NATIVE_WRITE_VERIFIED`. This testing and key/token enrollment need **new exact Human authorization**.

## AC and negative tests

Positive offline: real OpenSSH signature verifies, exact request intent persists across ledger reopen, no content leaks, custody classifier returns explicit HOLD. Negative: forged/tampered signature, changed source/Issue/expiry, replay and signed conflicting content, invalid key policy, same-SID credential claims, false proof of unattended readiness, no creation of the target file. Windows-only hosted MT01 owner/consumer and exact-head CI + independent Oracle are required for source acceptance; host/live evidence is separate.

**Acceptance ceiling for this source PR**: `W1_OFFLINE_INTENT_SOURCE_PASS` after independent review. `W1_SIGNED_NATIVE_WRITE_VERIFIED=NO`; `ALWAYS_ON_SECURE=UNPROVEN`; `WRITE_ENABLED=FALSE`; `NO_HOST_EFFECT`.
