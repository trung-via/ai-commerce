# HostOps W1.3 — Windows root-relative create-new proof (source/test candidate)

Parent #309, architecture #293, W1.1/W1.2 merged PR #313. One Windows sign-in account only.

## Human authorization

2026-11-03 user authorized W1.3 engineering source, Windows CI, Brain review, and one separately signed native scratch-file proof, plus closely scoped setup. This authorization is NOT a cryptographic Human Ed25519 signature, protected external credential custody, approved absolute scratch-root mapping, or evidence that the actual laptop was reached. Never generate a signature, private key, token or disk receipt on the user's behalf.

## Source cut

Existing W1.1 auth.personal_w1_verify_offline requires a Human OpenSSH Ed25519 signature binding request ID, private control Issue, source SHA, root ID human-scratch-v1, filename, UTF-8 bytes and SHA256, <=600 seconds validity. W1.2 OfflineW1IntentLedger durably reserves UNKNOWN and rejects replay/conflicts, but is not independently anti-rollback safe when public Actions runner shares the Windows SID.

W1.3 adds native filesystem fixture primitives only to the registered HostOps filesystem owner:

- w1_windows_root_identity(root): opens an absolute local directory without reparse points (including ancestors), obtains volume serial and file index from its handle, emits a CANDIDATE identity. A same-token local read does NOT attest that identity for trust; a Human or isolated external authority must pin it.
- w1_windows_native_create_fixture(root, expected_root_identity, filename, content): requires strict flat ASCII .txt|.md|.json filename, 1–2048 UTF-8 bytes, exact expected volume serial/file index. Uses Windows root directory HANDLE and NtCreateFile with RootDirectory, FILE_CREATE, FILE_OPEN_REPARSE_POINT, FILE_NON_DIRECTORY_FILE and synchronous I/O. Refuses existing names. Flushes content and checks actual bytes and SHA256; returns NATIVE_FIXTURE_CREATE_VERIFIED with real_host_attested=false, remote_write_enabled=false, credential_custody_proven=false. No deletion, overwrite, shell, elevation or Issue-selected directory.
- Integration fixture combines a REAL test-only OpenSSH signed proposal with SQLite intent reservation before creating a temporary Windows hosted CI fixture file; duplicate request stays HOLD_REPLAY_UNKNOWN. Fixture signing keys are NOT Human signer enrollment.

Functions are NOT imported into HostOps CLI, polling transport, broker, worker, Windows service, EF02 or self-host runner entrypoint. They are not a remote file-write permission or proof of safe unattended operation.

## Required verification

Exact-head Windows-hosted CI, MT-01 HostOps TARGETED/SCOPED_CONSUMER_CLOSURE, Windows native positive/negative fixture tests, Node 48 tests, Oracle and exact SHA/JUnit artifact reconciliation. No Ubuntu and no hosted-CI claim about laptop self-host configuration.

## Native acceptance packet (NOT EXECUTED)

- Target: Human-approved exact Windows scratch directory mapped out-of-band to root ID human-scratch-v1; pin volume serial/file index and trusted release SHA independently.
- File: w1-native-proof.txt, created ONCE with exact UTF-8 text AI_COMMERCE_W1_NATIVE_PROOF_V1.
- Future live request must bind merged source_sha, private GitHub Issue and immutable request ID, name, UTF-8 SHA256/size, Ed25519 namespace hostops-w1-create@ai-commerce, independently protected signer, token context, <=600 seconds expiry. Current source SHA/request ID/issue number for that live effect do not exist yet and must not be invented.
- Before native dispatch: demonstrate signer and verifier policy inaccessible to public same-SID runner; verify root pin, KILL/revocation and an anti-rollback journal outside same-SID control; implement reviewed signed W1.3 native CLI/route with no unattended PAT; require exact per-request signature.
- Execute one native proof under non-elevated current Windows user in a supervised session; independently inspect file SHA/size and signed durable receipt with source, issue, request pins. Never automatically retry an UNKNOWN. Revoke temporary credentials.

AC for THIS source PR: W1_3_NATIVE_FIXTURE_SOURCE_PASS after CI/Brain review. SIGNED_NATIVE_WRITE_VERIFIED=NO, W1_REMOTE_WRITE_ENABLED=FALSE, ALWAYS_ON_SECURE=FALSE until actual native evidence. No additional Windows account, service, private token or arbitrary PowerShell.

## Negative controls

Wrong root identity, reparse, non-local root, protected directories, traversal/UNC/ADS/device aliases, unsupported extension, content >2048 bytes, invalid UTF-8/NUL, existing file, signed request tamper, replay and UNKNOWN fail closed. A local claim of external signer/broker does not attest isolation. Unsigned GitHub Issues, test fixture keys, a desktop consent phrase, CI or chat approval alone do not activate writes.
