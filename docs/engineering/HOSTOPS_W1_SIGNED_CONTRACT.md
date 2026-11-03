# HostOps W1 — independently signed create-new proposal (SOURCE ONLY)

Work item: #309. This code does not install or run a laptop writer or unattended broker. R1 and R2 read-only native evidence remains separate.

## Threat model and architecture decision

Exactly ONE Windows account. A public-repository self-hosted Actions runner sharing that SID can compromise local files and process credentials. NTFS, DPAPI, Credential Manager, and in-process HMAC do not create isolation between same-SID malicious processes. GitHub explicitly warns against public-repository self-hosted runners: https://docs.github.com/en/actions/reference/security/secure-use .

Use an EXTERNALLY ISOLATED Human signing key and credential custody. A GitHub App installation token can be short-lived (one hour) but its minting private key must not be present on the same-SID Windows laptop. Theft of a live token remains possible. Reference: https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/best-practices-for-creating-a-github-app .

## Exact source contract

Schema hostops.personal.signed_create_proposal.v1 uses an immutable canonical JSON payload. Fields: schema, request_id (w1- followed by 32 lowercase hex characters), issue_number (exact positive GitHub Issue number), source_repository and control_repository (two fixed repositories), source_sha (exact public source commit), operation=create_new_utf8, parameters, issued_at and expires_at (UTC seconds, maximum 600 seconds), approval_signature_b64 (independent Human OpenSSH signature over all other fields).

Parameters include exactly root_id=human-scratch-v1, filename (ASCII flat basename, limited to safe alphanumeric/hyphen/underscore and txt/md/json), content_utf8 (valid UTF-8, 1–2048 bytes, NUL denied), content_sha256 and content_bytes. No Issue-selected absolute root, directory hierarchy, device aliases, paths, UNC, reparse points or NTFS ADS can be specified. Root ID is a label only: actual approved root has NOT been enrolled.

OpenSSH verification: ssh-keygen -Y verify, namespace hostops-w1-create@ai-commerce and signer identity hostops-human. One exact Ed25519 allowed signer is required; allowed-signers file and verifier executable both require pinned SHA256. Reference: https://man.openbsd.org/ssh-keygen.1 . Pins supplied by malicious same-SID config are not a credible trust anchor: external authority enrollment is still mandatory.

Existing src/ai_commerce/hostops_v3/auth.py exposes personal_w1_signing_payload and personal_w1_verify_offline. On successful real cryptographic check it returns only a sanitized record with status SIGNED_PROPOSAL_VERIFIED_NO_EFFECT, execution_allowed=false, replay_ledger_proven=false and credential_custody_proven=false. No active W1 CLI, GitHub poller operation, file effect, scheduler or credential storage is added.

## Remaining gates before native write

1. Separate exact Human authorization and protected public signer/source policy enrollment; private signing key outside the Windows SID and public runner.
2. Authenticated private Issue origin and owner, signature over Issue number and full content, TTL/revocation and immutable source pin.
3. Durable at-most-once intent recorded BEFORE a file effect; UNKNOWN cannot auto-retry; independent disk SHA reconciliation.
4. Human-approved scratch root with Windows handle-level reparse/junction safeguards; create-new no-overwrite and no arbitrary filesystem paths.
5. Windows self-hosted negative/live test and separate authorization. No persistent private token or unattended agent until an external credential trust boundary is demonstrated.

Expected levels: W1_SOURCE_SIGNATURE_GATE only after exact-head CI; W1_NATIVE_WRITE=NOT_ENABLED; UNATTENDED_CREDENTIAL_CUSTODY=UNPROVEN; REMOTE_WRITE_READY=false. R1/R2 live reads do not establish any of these missing write capabilities.
