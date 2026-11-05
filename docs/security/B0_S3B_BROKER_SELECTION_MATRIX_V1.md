# B0-S.3b Off-Host Broker Selection Matrix v1

Status: **S3B_SELECTION_AND_ATTESTATION_PLAN_ONLY / HUMAN_PLATFORM_AND_AUTHENTICATOR_SELECTION_PENDING**.
`PRIVATE_CUSTODY_VERIFIED` remains **UNKNOWN**.
Exact source baseline: `c7ae34c5a670277aaadbd422404fd41a06959d11` (`main`, following merged B0-S.3a PR #374 and B0-S.1/S.2 PR #365; research context [Issue #356](https://github.com/trung-via/ai-commerce/issues/356); [PA2 Roadmap B0-S.3 G05/G08](../roadmap/ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md)).
Human source-only authorization (2026-11-05): `"ok tiếp tục, sử dụng antigravity gemini 4 argon medium"` (`HUMAN_EXPLICIT` executor pin `antigravity / gemini-4-argon / medium`).

This document is a vendor-neutral architectural comparison and selection protocol only. **No cloud vendor, account, subscription,authenticator device, secret, key, or live broker service has been chosen, purchased, enrolled, or contacted.** All external effects remain blocked by [B0-S.2](B0_S2_AUTHORITY_PROTOCOL_V1.md) (`HOLD_PRIVATE_VERIFIER_NOT_ENROLLED`) and [B0-S.3a](B0_S3_ENROLLMENT_REVOCATION_PROTOCOL_V1.md) (`ISSUER_NOT_ENROLLED`).

---

## 1. Trust Zones and Conditional Preferred Architecture Pattern

The four-zone boundary established in [B0-S.1](B0_S1_TRUST_ZONE_INVENTORY_V1.md) and [B0-S.3a](B0_S3_CUSTODY_ARCHITECTURE_V1.md) is preserved unchanged:

- **Z0 (Public GitHub / Repository / CI / Issues / PRs):** Untrusted for private custody or runtime execution authority. Source code, schemas, comments, and CI `PASS` receipts never grant provider execution or secret access.
- **Z1 (Engineering Runner on Same Windows SID):** Untrusted for production secret custody and transaction approval. Executes public/candidate code; any secret, bearer token, DPAPI blob, Windows Credential Manager entry, local file, or cloud deployment token reachable by this Windows SID is assumed exposed under Z1 compromise.
- **Z2 (Human-Attended Offline PULSE Runtime):** Runs B0.1–B0.4 local drafting, FFmpeg rendering, QA, and local journal coordination. Z2 holds no live provider bearer credentials and cannot self-authorize external effects.
- **Z3 (Planned External Independent Broker — NOT SELECTED / NOT ENROLLED):** Separately administered off-host boundary that holds provider credentials, maintains durable outside-SID state indexes, verifies independent Human WYSIWYS consent and strategy/rights/entitlement proofs, reserves budget atomically before any provider call, executes the provider API call itself, and returns only a redacted receipt reference to Z2.

### Conditional Preferred Architecture Pattern (Vendor-Neutral)

Brain recommends—conditionally, awaiting explicit Human vendor/device selection and independent live attestation—an **Off-Host Policy-Enforcing Transaction Broker** with the following mandatory invariants:

1. **Direct Broker Custody and Execution:** Only Z3 holds provider credentials and only Z3 invokes the external provider API. Z3 **never** returns a raw provider bearer token, API key, session cookie, or signing key to Z1 or Z2.
2. **Durable Outside-SID State Registers:** Z3 owns strongly consistent, atomic storage outside the Windows runner SID and outside GitHub Actions for:
   - Single-use challenge nonces (`used_nonces`)
   - Stable request idempotency keys (`used_requests`)
   - Canonical semantic target digests (`used_targets` covering `provider_ref`, `account_ref`, `surface`, `action`, `semantic_target_ref`)
   - Monotonic revocation epochs and tombstones (`minimum_epoch`)
   - Pre-call atomic currency/spend reservations (`reservation_ref`, `spend_ceiling_minor`)
   - Unresolved `UNKNOWN_EFFECT` holds
3. **Full Transaction Policy Enforcement Inside Z3:** Credential binding in Worker environment variables or Cloud KMS encrypt/decrypt/sign permissions **alone** is NOT transaction policy enforcement. Before any provider call, Z3 must verify:
   - Exact `source_sha`, `journey_id`, `directive_version`, `directive_sha256`
   - Exact submitted `final_media_sha256` bytes/digest (never a mutable preview URL)
   - Exact `provider_ref`, `account_ref`, `surface`, `action`, `semantic_target_ref`, `semantic_target_sha256`
   - Separate `brain_decision_ref`/`sha256`, `human_grant_ref`/`sha256`, `provider_entitlement_ref`/`sha256`, `asset_rights_ref`/`sha256`, and `spend_grant_ref`/`sha256`
   - Half-open timestamp window (`issued_at <= at < expires_at`), `policy_audience`, `issuer_ref`, `public_key_id`, and `revocation_epoch >= minimum_epoch`
   - Currency and integer minor-unit spend bounds (`0 <= spend_minor <= spend_ceiling_minor`)
4. **Separate Trusted-Device Human WYSIWYS Consent:** Planned Human authentication occurs on a separate trusted device (e.g., mobile/hardware authenticator). WebAuthn User Verification (`uv=true`) may authenticate the Human identity to Z3, but **WebAuthn UV alone is not transaction confirmation**. The Human must view the complete transaction summary and `transaction_sha256` on an independent display surface not controlled by Z1 and bind approval to that exact digest and fresh Z3 challenge.
5. **Compromised Cloud Deploy Admin Threat Model:** If Z1, a GitHub Actions workflow, or a same-SID developer profile possesses cloud deployment or IAM admin credentials for Z3, Z1 can rewrite broker code to exfiltrate secrets or bypass WYSIWYS. Z3 deployment administration and independent auditing must be strictly separated from the Z1 Windows SID and Z0 GitHub workflows.

---

## 2. Candidate Off-Host Patterns: Primary-Sourced Comparative Matrix

All external documentation references below are architecture specifications carried from primary vendor/standards documentation as of the noted review context (2026-11-05). No live network fetch, vendor account trial, or pricing quote was executed in this cut; unpublished or account-specific operational behaviors and exact current fee schedules are marked **UNKNOWN**.

| Evaluation Dimension | Pattern P1: Edge Worker + Durable Transactional Object + Secret Binding (e.g., Cloudflare Workers + Durable Objects) | Pattern P2: Cloud Managed Compute + Vault + Managed Identity + Transactional DB (e.g., Azure Container Apps / Functions + Key Vault) | Pattern P3: Serverless Container + Secret Manager + Workload Identity + Transactional DB (e.g., Google Cloud Run + Secret Manager + Firestore/Spanner) | Pattern P4: Separate Dedicated Device / Host Broker (Physical or VM Appliance on Separate Admin Boundary) | Rejected Baselines (Same-SID Service / DPAPI / KMS-Only / Passkey-Only) |
|---|---|---|---|---|---|
| **1. Credential Custody Separation vs. Same-SID Runner (Z1)** | Secrets bound to off-host Worker runtime; not readable by Z1 Windows SID **if and only if** Z1 holds no Cloudflare API token, `wrangler` session, or account admin session. | Secrets held in Key Vault and accessed by Managed Identity inside Azure runtime; isolated from Z1 **only if** Z1 holds no `az` CLI token, Service Principal, or RBAC write role. | Secrets held in Secret Manager and mounted/fetched by Cloud Run service account; isolated from Z1 **only if** Z1 holds no `gcloud` ADC/token or IAM admin role. | Secrets held on separate physical/OS device outside the Windows laptop SID; isolated **only if** no SSH/admin key or management agent is stored on Z1. | **FAIL:** Same-SID DPAPI/Credential Manager/local service allows same-logon callers to read or invoke secrets. Passkey/KMS alone does not hold provider tokens or mediate full policy. |
| **2. GitHub / Z1 Deployment Privilege & Compromised Deploy Admin** | **HIGH RISK if unseparated:** Any actor with `Workers Scripts:Edit` can deploy code that reads bound secrets and exfiltrates them. Requires separate human-only deploy admin + immutable code hash audit. | **HIGH RISK if unseparated:** Any actor with Contributor/Deployment rights or Key Vault Officer role can alter container image or read secrets. Requires separate tenant/subscription admin + image digest pinning. | **HIGH RISK if unseparated:** Any actor with `run.services.update` or `iam.serviceAccounts.actAs` on the broker SA can deploy a secret-dumping revision. Requires separate project/admin + binary/digest attestation. | **HIGH RISK if unseparated:** Shared LAN/management credentials on the engineering laptop defeat isolation. Requires independent device admin, signed updates, and out-of-band console. | **FAIL:** Z1 same-SID or Z0 GitHub workflow directly controls execution or key invocation. |
| **3. Independent Human WYSIWYS Display & Auth Binding** | Can serve an authenticated approval endpoint to a separate mobile/hardware device and verify WebAuthn assertion + explicit digest confirmation; display independence depends on using a separate Human device, not the Z1 browser. | Same: can host approval UI/API for a separate Human device; requires strict origin isolation and out-of-band transaction digest confirmation beyond WebAuthn UV. | Same: can host approval UI/API for a separate Human device; requires independent device display of full transaction fields and `transaction_sha256`. | Can use a dedicated local hardware display or separate mobile device; must still prove display channel cannot be spoofed by Z1. | **FAIL:** Z1-rendered preview or WebAuthn UV alone without independent WYSIWYS display of final media/account/spend. |
| **4. Anti-Replay (Nonce, Idempotency Key, Semantic Target)** | Single-threaded Durable Object storage provides strongly consistent transactional key-value/SQLite state per coordination shard when routed through a single global coordinator object. Cross-shard ordering requires single-coordinator design. | Requires external transactional store (e.g., Azure SQL / Cosmos DB with strong consistency) with unique constraints on `nonce`, `idempotency_key`, and `semantic_target_sha256`. Compute instances themselves are stateless. | Requires transactional store (e.g., Cloud Spanner or Firestore in strong transactional mode / Cloud SQL) enforcing uniqueness atomically before egress. Cloud Run instances are ephemeral/stateless. | Local transactional SQLite (`SERIALIZABLE` / WAL with synchronous durability) on dedicated storage; single-node failure risks state loss unless replicated with anti-rollback anchor. | **FAIL:** Local worktree journal or in-memory state can be rolled back or wiped by Z1. |
| **5. Atomic Budget Reservation & `UNKNOWN_EFFECT` Retention** | Supported if reservation, nonce consumption, and `UNKNOWN_EFFECT` lock are committed in Durable Object storage **before** `fetch()` to provider, and retained until verified receipt reconciliation. | Supported via ACID database transaction committed before provider HTTP call; requires strict timeout/crash handling so uncommitted or timed-out calls stay `UNKNOWN_EFFECT`. | Supported via ACID transaction in Spanner/Cloud SQL/Firestore before provider HTTP call; requires holding reservation across container restarts. | Supported via local ACID transaction prior to provider call; requires monotonic disk flush and out-of-band backup to prevent state rollback on power loss. | **FAIL:** Post-call accounting or auto-retry/rekey on timeout violates spend ceiling safety. |
| **6. Secret Rotation & Epoch Advancement** | Secret version update via API/dashboard + monotonic `minimum_epoch` stored in Durable Object; active Worker isolates may require coordinated reload/epoch check on every request. Exact propagation SLA: **UNKNOWN**. | Key Vault versioned secrets + monotonic epoch in DB; Managed Identity caches tokens unlessbroker checks DB epoch per request and re-fetches/invalidates cached secret. Propagation SLA: **UNKNOWN**. | Secret Manager version aliases + DB epoch check; pinned version or per-request epoch check required because mounted env secrets require revision redeploy or direct API read. Propagation SLA: **UNKNOWN**. | Manual or device-daemon rotation of provider credentials and epoch counter; no cloud control-plane API unless built and maintained by Human. | **FAIL:** Repo-based epoch or same-SID rotation can be rolled back by Z1. |
| **7. Kill / Revocation Latency** | Immediate if checked against strongly consistent Durable Object epoch on every request before provider call; platform-level route disable propagation across edge POPs is **UNKNOWN**. | Immediate if checked against transactional DB on every request before provider call; Azure RBAC / Managed Identity token revocation has caching delay (**UNKNOWN**, often minutes to hours). | Immediate if checked against transactional DB on every request before provider call; IAM policy propagation and OAuth token expiry have caching delay (**UNKNOWN**). | Immediate on local device state if reachable; if device is offline or network-partitioned from Human kill switch, remote kill is blocked until device connectivity resumes (fail-closed if lease expires). | **FAIL:** Cannot reliably stop a compromised same-SID process holding a copied bearer token. |
| **8. Audit Logs, Availability, Fees & Operational Burden** | Managed global edge availability; logs require separate immutable sink so deploy admin cannot delete them. Usage-based pricing exists (free/paid tiers vary by vendor schedule: **UNKNOWN_WITHOUT_LIVE_VENDOR_QUOTE**). Low OS patching burden. | Managed regional availability; requires VNet/Key Vault/DB/Log Analytics setup and separate immutable audit storage. Pricing varies by SKU/tier (**UNKNOWN_WITHOUT_LIVE_VENDOR_QUOTE**). Moderate cloud IAM complexity. | Managed regional availability; requires Cloud Run/Secret Manager/DB/Cloud Logging sink setup. Pricing varies by region/tier (**UNKNOWN_WITHOUT_LIVE_VENDOR_QUOTE**). Moderate cloud IAM complexity. | No cloud vendor lock-in, but **HIGH physical/OS operational burden**: hardware provisioning, UPS/network, OS patching, TLS renewal, encrypted backup, and anti-rollback protection fall on Human. Costs: **UNKNOWN**. | Unacceptable security posture regardless of low initial setup cost. |

---

## 3. Primary Source References and Explicit Caveats

The URLs below are cited as architectural reference specifications only (reviewed context date: **2026-11-05**). None was fetched over the network during this execution, and none constitutes an endorsement, vendor selection, or compliance certification:

1. **Cloudflare Workers & Durable Objects Documentation:**
   - Workers Secrets: `https://developers.cloudflare.com/workers/configuration/secrets/` — Documents environment-bound encrypted variables exposed to Worker code at runtime. *Caveat:* Any principal permitted to deploy Worker code can read bound secrets inside that Worker; secret binding alone does not enforce transaction policy.
   - Durable Objects: `https://developers.cloudflare.com/durable-objects/` — Documents single-point-of-coordination in-memory + transactional disk storage per object instance. *Caveat:* Strong consistency applies per Durable Object instance; cross-object or misrouted requests without a single canonical coordinator do not provide global serializability. Exact platform kill propagation and current pricing are **UNKNOWN**.
2. **Microsoft Azure Managed Identities, Key Vault & DPAPI:**
   - Managed Identities for Azure Resources: `https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/overview` — Eliminates static credentials in app config for Azure-to-Azure auth. *Caveat:* Token caching and RBAC propagation delays mean IAM revocation is not an instantaneous per-transaction kill switch; an application-level transactional epoch check is still required.
   - Azure Key Vault Security: `https://learn.microsoft.com/en-us/azure/key-vault/general/security-features` — Hardware/software vault for secrets and keys. *Caveat:* Key Vault access policies/RBAC do not inspect PULSE media digests, creative directives, or WYSIWYS transaction consent.
   - Windows DPAPI (`CryptProtectData`): `https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata` — Binds encryption to the Windows user/machine logon context. *Caveat:* Any process running under the same Windows SID (`Z1`) can decrypt same-user DPAPI blobs.
3. **Google Cloud Run & Secret Manager:**
   - Secret Manager Overview: `https://cloud.google.com/secret-manager/docs/overview` — Versioned secret storage with IAM audit logging. *Caveat:* Mounting a secret or granting `secretmanager.versions.access` to a runtime service account allows any code deployed to that service account to exfiltrate the secret.
   - Cloud Run Security: `https://cloud.google.com/run/docs/securing/security` — Serverless container execution with per-service IAM identity. *Caveat:* Stateless containers cannot enforce anti-replay or atomic budget reservation without an external strongly consistent database.
4. **Authentication & Transaction Authorization Standards:**
   - W3C WebAuthn Level 3 (`https://www.w3.org/TR/webauthn-3/#sctn-verifying-assertion`): Defines cryptographic authenticator assertion verification (`rpIdHash`, `flags`, `signCount`, challenge binding). *Caveat:* Standard WebAuthn `clientDataJSON.challenge` and `uv=true` do not guarantee that the authenticator screen displayed the full commerce transaction (media SHA-256, target account, currency, spend ceiling).
   - OWASP Transaction Authorization Cheat Sheet (`https://cheatsheetseries.owasp.org/cheatsheets/Transaction_Authorization_Cheat_Sheet.html`) & NIST SP 800-63B-4 (`https://pages.nist.gov/800-63-4/sp800-63b.html`) & NIST SP 800-207 (`https://csrc.nist.gov/pubs/sp/800/207/final`): Require explicit per-transaction context binding, independent confirmation channel, replay resistance, and policy enforcement point separation.
   - GitHub Self-Hosted Runner Hardening (`https://docs.github.com/en/actions/reference/security/secure-use#hardening-for-self-hosted-runners`): Treats self-hosted runner hosts as exposed to workflow code execution.

---

## 4. Vendor-Neutral Decision Protocol and Selection Gates

Neither Brain nor EF01/EF02 may select a vendor, open an account, or enroll a key on behalf of the Human. Selection must follow this fail-closed protocol:

1. **Step 1 — Architecture Pattern Gate (Source Readiness):** Verify in offline synthetic tests that the proposed broker contract enforces all gates (`G01`–`G10` below) and rejects KMS-only, Passkey-UV-only, Same-SID, and self-certified broker claims. Maximum status achievable in source: `DESIGN_READY_FOR_HUMAN_SELECTION`.
2. **Step 2 — Explicit Human Platform & Device Selection (Future Out-of-Band Gate):** Human explicitly selects:
   - One off-host execution & strongly consistent storage platform (e.g., Pattern P1, P2, P3, or P4)
   - One separate Human trusted display/authentication device (physically and administratively distinct from the Z1 Windows runner)
   - One independent audit sink / auditor identity outside Z1 and outside the broker's own runtime self-assertions
   - Explicit initial currency and minor-unit spend ceiling (`0` for no-spend trials)
3. **Step 3 — Credential-Free Trial Tasks (Positive & Negative):** Before any real commerce provider credential is created or loaded, run the synthetic canary and negative attack trials in Section 5 and [B0_S3B_ISOLATION_ATTESTATION_PLAN_V1.md](B0_S3B_ISOLATION_ATTESTATION_PLAN_V1.md).
4. **Step 4 — Fail-Closed Rule:** If any gate `G01`–`G10` is `UNPROVEN`, `UNKNOWN`, or fails during trial, Z3 remains `UNENROLLED` and all external effects stay at `HOLD_PRIVATE_VERIFIER_NOT_ENROLLED`.

### Mandatory Selection Gates (`G01`–`G10`)

| Gate ID | Gate Name | Pass Criterion (Future Independent Evidence Required) | Fail-Closed Outcome if Missing/Violated |
|---|---|---|---|
| `G01` | **No Same-SID Secret or Admin Reachability** | Zero broker secrets, cloud deploy tokens, CLI sessions (`wrangler`, `az`, `gcloud`, SSH), or recovery keys exist on the Z1 Windows SID, Credential Manager, DPAPI, env, or filesystem. | `HOLD_SAME_SID_ACCESS` |
| `G02` | **Deploy Admin & Auditor Separation** | Cloud/device deployment admin is isolated from Z1 and Z0 GitHub Actions; deployed broker code digest is pinned and logged to an independent read-only audit sink that the broker runtime cannot overwrite. | `HOLD_CLOUD_DEPLOY_ADMIN_OVERLAP` |
| `G03` | **No Bearer Export / Full Broker Mediation** | Broker never returns provider bearer tokens, API keys, or presigned unconstrained upload/post capabilities to Z1/Z2; KMS/secret binding is paired with full in-broker transaction policy verification. | `HOLD_LONG_LIVED_BEARER_OR_KEY_ONLY` |
| `G04` | **Independent Trusted-Device WYSIWYS** | Human views full transaction details (`final_media_sha256`, `provider_ref`, `account_ref`, `surface`, `action`, `currency`, `spend_ceiling_minor`, `transaction_sha256`, TTL) on a separate device and approves that exact digest. WebAuthn UV alone is rejected. | `HOLD_UNTRUSTED_HUMAN_DISPLAY` |
| `G05` | **Durable Atomic Anti-Replay Store** | Strongly consistent outside-SID store atomically records `nonce`, `idempotency_key`, and `semantic_target_sha256` before any provider call; blocks rekeying across journey/nonce/media/directive edits. | `HOLD_REPLAY_OR_IDEMPOTENCY_UNPROVEN` |
| `G06` | **Atomic Pre-Call Spend Reservation** | Maximum exposure (`spend_ceiling_minor`) is atomically reserved in Z3 before any provider call and linked to `spend_grant_ref`/`sha256` and `reservation_ref`. | `HOLD_BUDGET_RESERVATION_UNPROVEN` |
| `G07` | **Bounded Revocation & Incident Kill Latency** | Monotonic `minimum_epoch` and emergency `KILL` are checked in strongly consistent Z3 storage immediately before every provider call, with measured revocation latency within the Human-approved SLA (`<= 300s`). | `HOLD_REVOKE_LATENCY_OR_EPOCH` |
| `G08` | **`UNKNOWN_EFFECT` Retention Without Retry** | Any timeout, crash, or unauthenticated provider receipt transitions the reservation to `UNKNOWN_EFFECT`, retaining original `idempotency_key` and budget reservation and blocking retry/rekey. | `HOLD_UNKNOWN_EFFECT_RETAINED` |
| `G09` | **Exact Transaction & Rights Scope Pinning** | Broker verifies exact match of `source_sha`, `directive_version`, `directive_sha256`, `final_media_sha256`, `brain_decision`, `human_grant`, `provider_entitlement`, and `asset_rights` before execution. | `HOLD_ROLLBACK_OR_SCOPE_TAMPER` |
| `G10` | **Out-of-Band Root Enrollment & Independent Auditor** | Trust root (`trust_root_sha256`, `issuer_ref`, `public_key_id`) is enrolled via out-of-band Human ceremony and attested by an independent auditor; broker self-certification objects are rejected. | `HOLD_SELF_CERTIFIED_OR_UNPROVEN_AUDITOR` |

---

## 5. Credential-Free Vendor Trial Tasks (Positive & Negative)

All trial tasks below use **synthetic non-secret canary strings** (e.g., `SYNTHETIC_CANARY_NO_PROVIDER_SECRET_001`) and a local/loopback or mock echo endpoint holding **zero real provider credentials and zero payment methods**:

1. **Trial T-POS-01 (Inert End-to-End Dry Run with Redacted Canary):**
   - Enroll a disposable test public key and synthetic canary in the candidate Z3 sandbox with `trial_mode="NO_CREDENTIALS_SYNTHETIC_ONLY"`.
   - Submit a canonical `BrokerTransactionDraft.v1` + `BrokerReceiptDraft.v1` and verify that Z3 displays the exact digest on the separate Human test device, reserves the synthetic budget slot atomically, and emits only a redacted receipt (`000...0` preview sentinel, zero canary leakage).
2. **Trial T-NEG-01 (Same-SID Exfiltration Attempt):**
   - From the Z1 Windows runner account, attempt to read environment variables, Windows Credential Manager, DPAPI blobs, filesystem caches, and cloud CLI profiles to recover the synthetic canary or deploy a modified broker script. **Required result:** Zero access; any deploy attempt from Z1 is denied and alerted.
3. **Trial T-NEG-02 (Z1 Media / Target Substitution After Human Prompt):**
   - Present Transaction A to the Human display, then have Z1 submit Transaction B (different `final_media_sha256`, `account_ref`, or `spend_minor`) using Transaction A's challenge/grant reference. **Required result:** Deterministic `HOLD_ROLLBACK_OR_SCOPE_TAMPER` / `SCOPE_MISMATCH`; zero effect.
4. **Trial T-NEG-03 (WebAuthn UV Without WYSIWYS Digest Binding):**
   - Submit a valid authenticator user-verification flag with `human_approval_mode="WEBAUTHN_UV_ONLY"` or mismatched `display_sha256`. **Required result:** Deterministic `HOLD_UNTRUSTED_HUMAN_DISPLAY`.
5. **Trial T-NEG-04 (Concurrent Replay & Semantic Target Rekey):**
   - Fire 12 parallel requests reusing the same `nonce`, same `idempotency_key`, or rekeyed `idempotency_key` targeting the same `semantic_target_sha256`. **Required result:** At most one reservation slot acquired in the trial store; all duplicates/rekeys rejected with `HOLD_REPLAY_OR_IDEMPOTENCY_UNPROVEN`.
6. **Trial T-NEG-05 (Epoch Revoke & Incident Kill During Flight):**
   - Advance `minimum_epoch` or trigger `KILL` immediately after Human consent but before broker execution. **Required result:** Execution aborted with `HOLD_REVOKE_LATENCY_OR_EPOCH`; old epoch grants permanently rejected.
7. **Trial T-NEG-06 (Simulated Provider Drop / `UNKNOWN_EFFECT`):**
   - Inject an ambiguous disconnect after pre-call reservation. Attempt to retry with the same key, retry with a fresh key, or release the reserved budget without an authenticated reconciliation receipt. **Required result:** All attempts blocked with `HOLD_UNKNOWN_EFFECT_RETAINED`; original key and budget reservation remain locked.
