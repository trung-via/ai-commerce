# HostOps W1.4 Phase 2 — Private GitHub REST origin binding / broker SSHSIG (source-only)

Parent: [HostOps W1 #309](https://github.com/trung-via/ai-commerce/issues/309); previous source-only ingress PR #324 merged at `main@4a94916c83f865e88c3cd2381b75fa5f4c312a77`. The actual W1.3 attended proof (private Issue #7, `w1-52bbb21e32b9097d8e4bca08a1227f84`) is **CONSUMED, NO_RETRY**. Nothing in this phase authorizes recreating or replacing its 30-byte target.

## Live GitHub connector discovery performed in this ChatGPT turn

An authenticated GitHub connector GET of the **private** `trung-via/ai-commerce-hostops-control` repository and its historical Issue #7 returned these stable REST identities:

- Repository ID **1443294096**, `private=true`, owner `trung-via`, owner account ID **301941572**.
- Issue #7 ID **6197463901**, author `trung-via` / account ID **301941572**, repository URL and Issue URL under `https://api.github.com/repos/trung-via/ai-commerce-hostops-control`, `state=open`.

Issue #7 is a **historical manual staging Issue**, **not** a new `HOSTOPS_W1_SIGNED_ISSUE_V1` body; it cannot be used to execute or reauthorize an effect. The connector read establishes the **chat-side authenticated GitHub connection only**. It does NOT prove any new private Windows broker, protected credential custody, TLS verification on a laptop, independent Windows disk receipt, or effect execution.

## Source implemented in this cut

`src/ai_commerce/hostops_v3/w14_origin.py` performs only **pure offline** validation and signature verification. There is no networking, service, credential handling, Windows native write, journal mutation, token, dispatch, Wake or PULSE action.

1. `normalize_private_issue_rest(repo, issue, issue_number=N)` checks that independently obtained REST objects match the exact API host/path, fixed private repo ID/owner ID, positive stable Issue ID, author ID/login, Issue number, open state, node ID, timestamp, and rejects pull requests masquerading as Issues. It constructs an untrusted typed snapshot plus SHA256 over exact ASCII body bytes. **The objects are still claims until their caller authenticates the HTTPS GitHub API response**.
2. `make_broker_statement(candidate, source_sha, observed_at, expires_at)` binds the Issue metadata, fixed repo/owner, Issue/body SHA256, source SHA, observation and TTL (maximum 120 seconds). It does not sign anything and never outputs an authorization grant.
3. `verify_broker_signed_issue(candidate, envelope, ...)` checks an **independent** OpenSSH Ed25519 signature over the canonical broker statement using namespace `hostops-w14-github-origin@ai-commerce` and principal `hostops-github-broker`. This broker public key MUST differ from Android/Human `hostops-human` and has an independently preapproved allowed-signers policy SHA256. It then verifies the separate *Human* W1 signature over the full native effect using the existing `w1_bridge.inspect_private_issue_snapshot`. Source pin, immutable Issue number, body bytes/hash, timestamp and both signatures are checked. Tests use **test-only** Human and issuer keys; no live broker key has been enrolled.
4. A successful returned record is `BROKER_AND_HUMAN_SIGNATURES_VERIFIED_NO_EFFECT`, with `broker_signature_verified=true`, `human_signature_verified=true`, but `github_origin_verified=false`, `broker_credential_custody_proven=false`, `anti_rollback_proven=false`, `dispatch_authorized=false`, `remote_write_enabled=false`, `independent_host_receipt=false`. This distinction is intentional. A broker signature is only as reliable as an independently verified broker and its protected credential custody.

## Credential custody and live-transport prerequisites not met

- A **separate external GitHub App / trusted broker process** must authenticate REST GET `/repos/trung-via/ai-commerce-hostops-control` and `/repos/.../issues/{issue}` with a *short-lived Issues:read scoped credential*, validate TLS without redirects, bound response bytes and immutable Issue IDs, and sign the exact broker statement with a **different key**.
- The private GitHub installation credential, broker private signing key, journal anti-rollback authority and token refresh must be **outside the Windows SID of the public self-hosted Actions runner**. Do not put a PAT, GitHub App private key, client secret, bearer token or broker signing key in a public repo, GitHub Issue, PR artifact, Actions log, machine environment under the shared SID, Windows Credential Manager/DPAPI under the same SID, or ChatGPT.
- Independent signer enrollment and immutable broker verification policy must be pinned outside the same untrusted SID. A passing signature from a *test fixture key* or policy file rewritten by the public runner cannot establish real trust.
- The current single-SID SQLite `UNKNOWN_UNRECONCILED` reservation is a useful local non-retry mechanism but **not** independently anti-rollback. A fenced, external intent / evidence authority, stop/revocation and a separate independently validated readback receipt are required before any remote effect.
- Even after a broker has read a signed Issue, the Human W1 payload may only authorize one strictly bounded, approved `create_new_utf8` request with TTL <=600 seconds. No arbitrary PowerShell, overwrite/delete, unattended shell, EF02/Codex, PULSE, media provider spend, or provider publication may be inferred.

## Acceptance and negative evidence

- Windows-hosted MT-01 selects the newly onboarded `hostops-w14-origin` plus real `hostops-w14` and `hostops-v3` consumers; Oracle PASS and Node test evidence required on **exact PR HEAD/base**.
- Positive: actual OpenSSH test-only independent broker + Human signatures, correct fixed repo/user/Issue REST metadata, body digest and source/TTL binding.
- Negative: wrong repo ID, private=false, owner/author ID, state/URL/Issue ID, PR object, wrong signature role/namespace/key, policy hash, body content changed after broker signature, expired broker TTL, invalid Human signature even when broker signs the changed body, unknown trusted-origin flags. No disk or network effect in any test.
- Failed earlier PR attempts or unrelated test failures remain documented; tests, trusted MT-01 registry and frozen A18 SHA may **not** be altered to bypass protection.

**Current acceptance ceiling:** `W14_ORIGIN_BROKER_SOURCE_VERIFIED` after independent Brain source review + exact Windows CI. `PRIVATE_GITHUB_ISSUE_CONNECTOR_READ_OBSERVED_IN_CHAT` is separate from `INDEPENDENT_LIVE_BROKER=NOT_PROVEN`. `REMOTE_WRITE_ENABLED=false`, `WINDOWS_REMOTE_EFFECT=NOT_EXECUTED`, `INDEPENDENT_HOST_RECEIPT=NOT_PROVEN`.

**Next smallest independent work:** establish *actual* external credential-broker custody and broker public-key enrollment, then perform a **read-only** signed Issue origin check with a *new* Issue and Human signature. Only after that separately implement fenced at-most-once Windows execution and independent host receipt.
