# HostOps W1.4 — Signed private GitHub Issue ingress, source-only foundation

Parent: [HostOps W1 #309](https://github.com/trung-via/ai-commerce/issues/309). Historical W1.3 attended success: private control Issue #7, request `w1-52bbb21e32b9097d8e4bca08a1227f84`, `main@76212ef690347ed380753da8192a56caaabf2425`. Human reported exact 30-byte file and separate read-only SHA256/SQLite reconciliation. GitHub/ChatGPT did **not** read the real Windows disk independently. The original Issue #6 and #7 signed requests are consumed and MUST NEVER be replayed.

## What is actually implemented in this phase

`src/ai_commerce/hostops_v3/w1_bridge.py` defines a strict offline envelope for a future authenticated private GitHub Issue fetch. No token, network, poller, Windows service, shell command, runner dispatch, filesystem write or re-signing capability is added.

1. `encode_signed_issue_body` formats **already independently Human-signed** W1 proposal JSON with one non-executable magic marker. No public key or signer private key is created.
2. `inspect_private_issue_snapshot` requires exact metadata shape, expected private repo, positive exact Issue number, open state and claimed GitHub author `trung-via`. This is **metadata CLAIM validation**, not an authenticated GitHub origin guarantee. The later adapter must prove TLS, owner/repo/Issue identity from the GitHub API, not from a submitted JSON map or same-SID env var.
3. Strict one-marker + canonical ASCII JSON; no Markdown fences, appended instructions, unknown keys, noncanonical whitespace, or arbitrary PowerShell/path/script fields. Real independent Ed25519 `ssh-keygen -Y verify` verifies the exact proposal, source SHA, private Issue number, root ID, 1–2048 UTF-8 bytes, fixed `create_new_utf8` bounds and <=600-second TTL.
4. Returned typed sanitized inspection **always** says `SIGNED_ISSUE_CONTENT_VERIFIED_NO_EFFECT`, `github_origin_verified=false`, `dispatch_authorized=false`, `credential_custody_proven=false`, `replay_prevented=false`, `independent_host_receipt=false`, `remote_write_enabled=false`. This function does not admit or reserve an intent, nor does it bind any self-host credential.

The signed Issue body format is `HOSTOPS_W1_SIGNED_ISSUE_V1` followed by LF and exactly one canonical signed JSON payload. Do not publish private credentials or any secret in an Issue. The proposal's detached Ed25519 SSHSIG is public authorization data, not the private key.

## Mandatory boundaries before real remote W1 write

| Boundary | W1.4 ingress phase | Required for later remote effect |
|---|---|---|
| Human Android private signer | Outside Windows; W1.3 demonstrated attended per-effect signature | Fresh newly signed immutable request bound to *new* private Issue/new source pin/root/content/TTL |
| GitHub Issue payload | Offline claim+signature gate | **Authenticated** GitHub API / app installation, verified owner and exact immutable Issue content/digest |
| GitHub token | No token stored or consumed | Independently managed **short-lived** private repo read credential, never same Windows SID as untrusted public self-host runner |
| Durable intent | Existing W1.2 same-SID SQLite, no new authority | Independently anti-rollback and fenced, atomic reserve before effect, duplicate or ambiguous result = HOLD_UNKNOWN |
| Windows file effect | Existing W1.3 *local attended* native create | Isolated authorized worker and explicit protected delegation; still create-new, no arbitrary shell or overwrite |
| Evidence | CI tests + Human W1.3 disk transcript | Independently authenticated host readback and cryptographically bound receipt, validated against Issue/source/request/intent |
| PULSE/EF02/Wake | No new route | Separate approval and runtime boundary, no automatic escalation |

Using a public Actions self-host runner under the same Windows SID **cannot provide independent token custody** through ordinary ACLs, Credential Manager, DPAPI or a file-mode check. A signed JSON body alone cannot authorize an effect or make a purportedly GitHub-originated snapshot authentic.

## Verification AC — this phase ONLY

- Real test-only SSH signed proposal accepted through canonical Issue body, with `execution_allowed` remaining false.
- Tampered body, changed Issue number, author/repo/state, malformed/noncanonical JSON, old TTL and incorrect source rejected with fixed `Denied` results.
- Strict schema excludes unknown trusted-origin flags; never upgrade an arbitrary local `origin_verified=true` claim to authority.
- No GitHub API calls, passwords/PAT, private key, ledger mutation, worker invocation, HostOps CLI execution or native file write introduced.
- Registered Windows MT-01 HostOps owner + consumers, Node and independent oracle succeed on exact PR HEAD. Brain review is separate from Engineering CI.

**Acceptance ceiling:** `W14_SIGNED_ISSUE_INGRESS_SOURCE=PASS` after real PR CI and Brain; `W14_LIVE_PRIVATE_GITHUB_ORIGIN=UNPROVEN`, `W14_REMOTE_WRITE=DISABLED`, `W14_INDEPENDENT_RECEIPT=UNPROVEN`.

**Next implementation cut (not yet authorized by this source package):** explicitly authenticate GitHub private Issue origin using separately protected short-lived credential broker, bind immutable Issue content SHA to an independent verifier/fenced durable ledger, and provide an isolated Windows ingress/attested readback test. Never rerun W1.3 Issue #7 request or treat its completed proof file as a new target.
