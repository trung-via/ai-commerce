# HostOps W1.3 — one-time supervised Windows setup (single user, no daemon)

Parent #309. Uses merged W1.1 signed request, W1.2 SQLite intent and W1.3 NT-handle create-new fixture. This source change adds **operator-invoked commands only**. No private GitHub Issue poller, no unattended credentials, no source-runner native dispatch and no automatic laptop activation. Windows-hosted CI exercises temporary fixture directories, not the owner's machine.

## Exact bounded scope

- Allowed local scratch folder (must already exist): `C:\TOOL\AI-Commerce-HostOps-Approved`.
- Allowed state folder (must already exist): `C:\TOOL\AI-Commerce-HostOps-State`.
- Single destination: `w1-native-proof.txt`, create-new ONLY; no overwrite or delete.
- Exact 30-byte ASCII/UTF-8 content: `AI_COMMERCE_W1_NATIVE_PROOF_V1`; SHA256 `cb75a8442d1e014b07f7d4f9c1336d60730a3822dad186b18588163062b8f220`.
- Current *source commit* (the git checkout must be clean) and actual private control Issue number are bound into a Human-signed request with TTL <=600 seconds. No signing key, PAT or token may be supplied to ChatGPT, GitHub Issue body or public self-host runner.
- Local console MUST be interactive and non-elevated. The only allowed input is the exact `CREATE W1 <issue> <request_id>` confirmation. A typed confirmation is **not** a security boundary against a malicious process under the same Windows SID.
- Stop switch: existing `C:\TOOL\AI-Commerce-HostOps-State\KILL` blocks execution. A request ID is reserved in `w1-intent.sqlite3` before writing. Ambiguous outcomes remain `UNKNOWN`; never re-run with the same signed request or delete/replace the journal to retry.

## Preparation / diagnostic (does not create a file)

On the existing Human Windows account, obtain a clean trusted checkout of *reviewed/merged* source. Inspect the exact HEAD. In PowerShell, from a trusted local terminal (NOT a public-repository self-host Actions job):

```powershell
$checkout = 'C:\TOOL\ai-commerce'
$sourceSha = git -C $checkout rev-parse HEAD
git -C $checkout status --porcelain --untracked-files=all
# Inspect the output and require an empty status, no implicit pull or checkout.
# Only after independent directory/root authorization:
New-Item -ItemType Directory -Path 'C:\TOOL\AI-Commerce-HostOps-Approved' -Force | Out-Null
New-Item -ItemType Directory -Path 'C:\TOOL\AI-Commerce-HostOps-State' -Force | Out-Null
$env:PYTHONPATH = "$checkout\src"
python -m ai_commerce.hostops_v3.cli personal-w1-root-check --source-root $checkout
```

The root-check output includes source SHA and candidate Windows volume serial/file index **only**. Review these values outside the untrusted public-runner environment. This is not independent root attestation or credential enrollment. Do not create any directory if the approved path differs from the agreed exact root.

## Signing/enrollment prerequisite

The operator must independently verify the allowed public signing key and `ssh-keygen.exe` hash, and keep the corresponding **private key outside the reach of any public Actions runner sharing the Windows SID**.

### W1 Android public signer enrollment (source policy only)

The user generated a dedicated Termux/OpenSSH Ed25519 key on a separate Android device. The **public** key from that device is registered (subject to merge) in `config/hostops/w1-human-allowed-signers`, with the single allowed principal `hostops-human` and label `ai-commerce-w1-human`. The distinct V2 public key remains untouched.

User-observed and programmatically re-derived SSH public-key fingerprint:

```text
SHA256:idxqzTG8hI/g3CL72aj9zzI+jEvXElaEg+40HrGbS9w
```

Exact one-line W1 signer-policy file SHA256 (UTF-8 with LF):

```text
f7c41e41d3429750aa423665f41b07c7c4a7ddeb6a3c776eee940fcbf85d5e26
```

For the attended Windows command, the user may use the **absolute path** `C:\\TOOL\\ai-commerce\\config\\hostops\\w1-human-allowed-signers` for `--allowed-signers`, after a fresh clean merge checkout and independent comparison against the Android fingerprint. Pass the exact file hash above for `--signers-sha256` only after out-of-band verification. Record the locally observed `ssh-keygen.exe` SHA256 separately; do not invent or reuse a hash from CI. The Android **private** key and passphrase stay in Termux, never in GitHub, a Windows filesystem, ChatGPT, public CI, logs, or a shared Android storage directory. Do not start sshd or ssh-agent services for this workflow.

A committed public signer is **not** evidence that the actual signer can produce a live signature, that the verifier is independently protected from same-SID public runner tampering, that the private Issue exists, or that any Windows native write was executed. Keep W1 remote transport disabled and require the per-effect Human SSHSIG and independent root/Issue/ledger checks before any effect.
 The old HostOps V2 key is not automatically eligible for W1; W1 requires the dedicated principal `hostops-human` and the namespace `hostops-w1-create@ai-commerce`. Do not silently repurpose a same-SID private key.

The signer must first inspect and sign canonical JSON consisting of exactly the W1 unsigned fields:

```text
schema = hostops.personal.signed_create_proposal.v1
request_id = w1-<32 fresh lowercase hex>
issue_number = <actual positive private control Issue number>
source_repository = trung-via/ai-commerce
control_repository = trung-via/ai-commerce-hostops-control
source_sha = <fresh verified merged checkout SHA>
operation = create_new_utf8
parameters = {root_id:human-scratch-v1, filename:w1-native-proof.txt,
              content_utf8:AI_COMMERCE_W1_NATIVE_PROOF_V1,
              content_sha256:cb75a8442d1e014b07f7d4f9c1336d60730a3822dad186b18588163062b8f220,
              content_bytes:30}
issued_at / expires_at = Unix UTC seconds; 0 < TTL <= 600
```

Canonical bytes: `json.dumps(unsigned, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n'` encoded as ASCII. Sign these bytes via `ssh-keygen -Y sign -n hostops-w1-create@ai-commerce` on the independently controlled signer. Base64-encode the SSHSIG and add it as `approval_signature_b64`. The resulting JSON may be transferred to the attended Windows console as a local file; it contains public request data/signature only. Do not print private key, passphrase or token.

## Execution command — DO NOT RUN UNTIL SIGNER + ISSUE + ROOT PINS ARE PROVEN

After separately verifying that the original private Issue was authored by the authenticated owner, signed payload matches it, the external signer policy and verifier binary hashes were independently approved, and the request is still fresh:

```powershell
python -m ai_commerce.hostops_v3.cli personal-w1-attended-once `
  --source-root $checkout `
  --request-file 'C:\W1-operator\signed-w1-request.json' `
  --issue <ACTUAL_ISSUE_NUMBER> `
  --allowed-signers 'C:\W1-operator\w1-allowed-signers' `
  --signers-sha256 <PINNED_64HEX_PUBLIC_POLICY_HASH> `
  --ssh-keygen 'C:\Windows\System32\OpenSSH\ssh-keygen.exe' `
  --verifier-sha256 <PINNED_64HEX_VERIFIER_BINARY_HASH> `
  --root-volume-serial <CANDIDATE_8HEX_INDEPENDENTLY_PINNED> `
  --root-file-index <CANDIDATE_16HEX_INDEPENDENTLY_PINNED>
```

The CLI rejects missing/expired/mismatched signature, issue, source, exact content, filename, root identity, an existing target file, active KILL, non-interactive/elevated execution, or replay. A successful execution prints only a sanitized local result and SHA; it **does not** publish a private GitHub Issue receipt or prove remote ChatGPT-to-Windows write. Human separately checks the file bytes/hash on the actual laptop, preserves evidence and revokes any temporary credential.

## W1.3 first Human-attended run — forensic hold and local-only diagnostics

The initial supervised native proof request `w1-8e82b920ac9a98defea56bf6dd91c47c`, private control Issue #6, source `26398290f8c2440eaa383783dbdf84f8d95610ab`, was attempted 2026-11-04 12:30:49–12:32:47 ICT. The signed request had an expiry of 12:34:21 ICT; its first-stage SSH signature validation passed and the exact confirmation prompt appeared, but the CLI emitted `HOSTOPS_V4_BLOCKED`. Human local read-only checks later found **no target file and no `w1-intent.sqlite3` ledger**. Independent historical-time verification at 12:32:47 passed for signature, policy hash, verifier hash, and source; the root and state identity checks also passed when re-evaluated later. This excludes the original request being expired when CLI returned, but does not prove the exact reason for the first block: typed consent mismatch, transient state, re-verification or local ledger open failure still require appropriate evidence.

**Historical request remains `NO_RETRY / CLOSED_FOR_EFFECT`** even if no intent was recorded. Preserve its signed packet and Issue #6 as evidence; do not use the old signed file again or delete/recreate any ledger to bypass UNKNOWN.

To improve diagnosis of a **future newly signed** supervised request, this change emits fixed, local-only checkpoints at these gates: `SIGNED_PRECONSENT`, `CONSENT_EXACT`, `STATE_RECHECK_PASSED`, `SIGNATURE_RECHECK_PASSED`, `INTENT_RESERVED_UNKNOWN`, and `NATIVE_READBACK_VERIFIED`. On an attended-only blocked return, it additionally displays `HOSTOPS_W1_LOCAL_DENIAL=<allowlisted static code>` or `OTHER_BLOCKED` while keeping the existing `HOSTOPS_V4_BLOCKED` and nonzero status. **Do not treat checkpoints as an independent audit receipt** or expose raw exceptions/signatures/content. Lack of `INTENT_RESERVED_UNKNOWN` is useful but not conclusive if output is lost. Once an intent is reserved, failures remain UNKNOWN and no automatic retry is authorized.

After this change is reviewed and merged, any future newly issued W1 request must bind the newly merged source SHA and a separately verified private control Issue; the old #6/source pair must not be reused. A fresh Android SSH signature and exact Human confirmation remain mandatory. No unattended transport is enabled.

## What this DOES NOT enable

No Windows service, new Windows account, GitHub Action self-host dispatch, credential persistence, broad read/write, source-write to arbitrary paths, unattended/polling access or E2E GitHub command route. The private repository Issues API is transport, not proof of independent Human authorization. Same-SID ACL/DPAPI/Credential Manager cannot guarantee protection from malicious public runner code. For a later remote signed W1 transport, independently establish external token/signing custody, anti-rollback trust root, authenticated GitHub private Issue binding and native disk reconciliation. Until then: `W1_REMOTE_WRITE_DISABLED` / `ALWAYS_ON_SECURE_UNPROVEN`.

Acceptance ceiling for this PR: `W1_ATTENDED_CLI_SOURCE_PASS` after exact HEAD Windows MT01/Node/Oracle + source review. Actual laptop effect requires a real Human-edited/signed packet and native receipt; this document is not a claim of live activation.
