# B0-S.3b Isolation Attestation Plan and Read-Only Installation Checklist v1

Status: **S3B_SELECTION_AND_ATTESTATION_PLAN_ONLY / LIVE_VERIFIED_IMPOSSIBLE_IN_THIS_CUT**.
Exact source baseline: `c7ae34c5a670277aaadbd422404fd41a06959d11` (`main`; merged B0-S.3a PR #374, B0-S.1/S.2 PR #365, research [Issue #356](https://github.com/trung-via/ai-commerce/issues/356)).
Human source-only authorization (2026-11-05): `"ok tiếp tục, sử dụng antigravity gemini 4 argon medium"`.

This plan defines the mandatory future isolation attestation protocol, the 20-class adversarial threat table (`T01`–`T20`), the stepwise read-only future installation checklist, and the no-secret redacted-canary verification procedure. **No live broker, cloud account, hardware key, or provider credential is enrolled or attested by this repository cut.**

---

## 1. Evidence Status Definitions (Strict Separation)

Every security control and readiness claim in B0-S.3b is classified into one of four mutually exclusive statuses:

| Status | Meaning | Achievable in B0-S.3b Source Cut? | Can Authorize Live Provider Effect? |
|---|---|---|---|
| `DESIGNED` | Normative specification, closed schema, and fail-closed state machine documented in source. | **YES** | **NO** |
| `SIMULATED_ONLY` | Deterministic offline synthetic tests (`test_b0_s3b_broker_readiness.py`) exercise positive inert previews and negative rejection/hold gates using in-memory fixtures. | **YES** | **NO** |
| `UNPROVEN` | Any claim requiring an enrolled external Z3 broker, physical/account separation from the Z1 Windows SID, independent Human WYSIWYS display hardware, durable outside-SID storage, or an out-of-band auditor. All simulated `"isolated"`, `"keys_safe"`, `"human_verified"`, and `"provider_ready"` claims are forced to `UNPROVEN` in this cut. | **YES (Default for all live claims)** | **NO** |
| `LIVE_VERIFIED` | Out-of-band independent auditor and Human have observed and signed off on real off-host broker isolation, non-reachability from the Z1 Windows SID, independent trusted-device WYSIWYS confirmation, atomic replay/budget durability, and emergency `KILL` latency under a separate live authorization. | **IMPOSSIBLE IN THIS CUT** | Required (alongside per-effect grants) in a future live stage |

---

## 2. Stepwise Future Read-Only Installation & Attestation Checklist

No step below may be executed under the current B0-S.3b source-only authorization. Each step requires a distinct future Human live authorization after the Human selects the target platform, trusted display device, and spending budget:

1. **Step 1 — Distinct Human Live Selection & Budget Ceiling Ceremony:**
   - Human selects the off-host platform pattern (from [B0_S3B_BROKER_SELECTION_MATRIX_V1.md](B0_S3B_BROKER_SELECTION_MATRIX_V1.md)), the dedicated Human WYSIWYS display/authenticator device, the independent auditor sink, and the initial currency/spend ceiling (`0` minor units for no-spend canary verification).
   - Verify that neither Z1 (`same Windows SID`) nor Z0 (`GitHub Actions`) holds admin, deployment, or secret-read permissions in the selected platform.
2. **Step 2 — Out-of-Band Z3 Trust Root & Operator/Dev Separation Enrollment:**
   - Generate and admit `issuer_ref`, `public_key_id`, and `trust_root_sha256` exclusively inside the off-host Z3 boundary and the Human's separate admin console.
   - Verify operator/developer separation: the engineering laptop Windows SID used by EF01/EF02 (`Z1`) and the attended offline PULSE worker (`Z2`) must have zero CLI tokens (`wrangler`, `az`, `gcloud`), SSH keys, service-principal secrets, or browser cookies capable of modifying Z3 code, reading Z3 secrets, or altering Z3 audit logs.
3. **Step 3 — Durable Outside-SID State & Monotonic Epoch Provisioning:**
   - Initialize the off-host strongly consistent transactional store for `used_nonces`, `used_requests` (`idempotency_key`), `used_targets` (`semantic_target_sha256`), `minimum_epoch` (starting at `>= 1`), and budget reservations (`reservation_ref`).
   - Verify that local Z1/Z2 filesystem edits or git resets cannot roll back Z3's `minimum_epoch` or erase consumed nonces/idempotency keys.
4. **Step 4 — Independent Human WYSIWYS Display & Device Binding:**
   - Bind the Human's separate trusted device to Z3.
   - Verify that every approval prompt independently renders: `source_sha`, `journey_id`, `directive_version`, `directive_sha256`, `final_media_sha256`, `provider_ref`, `account_ref`, `surface`, `action`, `semantic_target_ref`, `currency`, `spend_minor`, `spend_ceiling_minor`, `issued_at`/`expires_at`, and `transaction_sha256`.
   - Confirm that WebAuthn user verification (`uv=true`) without explicit binding to `display_sha256 == transaction_sha256` is rejected with `HOLD_UNTRUSTED_HUMAN_DISPLAY`.
5. **Step 5 — Independent No-Secret Canary & Out-of-Band Auditor Verification:**
   - Load only a **synthetic non-secret canary** (`SYNTHETIC_CANARY_NO_PROVIDER_SECRET_001`) into Z3's secret slot. Do **not** enroll real provider API keys yet.
   - Run all hard-criteria attacks (`T01`–`T20`) while an out-of-band auditor (independent of Z1 and distinct from Z3 self-report objects) records immutable logs and verifies that:
     - Z1 cannot read or exfiltrate the canary;
     - Z3 never echoes the canary in any response, error code, or preview;
     - Only the redacted digest (`canary_redaction_sha256`) and fixed preview sentinel (`0` * 64) appear in audit summaries.
6. **Step 6 — Emergency Epoch Revoke & `KILL` Latency Verification:**
   - Trigger an out-of-band `KILL` / epoch increment (`minimum_epoch + 1`) while a synthetic transaction is mid-challenge.
   - Auditor measures wall-clock latency from `KILL` issuance to deterministic rejection (`HOLD_REVOKE_LATENCY_OR_EPOCH`) at Z3 and verifies it is within the required bound (`<= 300` seconds) and that all prior-epoch grants are permanently invalidated.

---

## 3. Threat Table (`T01`–`T20`) and Hard Attack Criteria

| Threat ID | Threat / Attack Scenario | Target Boundary | Mandatory Fail-Closed Defense & Gate Code | Current B0-S.3b Status |
|---|---|---|---|---|
| `T01` | **Same-SID Secret Exfiltration:** Malicious code on Z1 Windows runner reads env vars, DPAPI, Windows Credential Manager, or local files to steal provider credentials. | Z1 -> Z3 | Zero provider credentials stored on Z1/Z2; any claim with `same_sid_access != "DENIED_CLAIMED"` or local custody mode fails closed with `HOLD_SAME_SID_ACCESS`. | `DESIGNED` / `SIMULATED_ONLY` (Live: `UNPROVEN`) |
| `T02` | **Compromised Cloud Deploy Admin Overlap:** Z1 runner or GitHub Actions workflow uses cloud deploy credentials (`wrangler`, `az`, `gcloud`, IAM admin) to push a backdoored broker that dumps secrets. | Z0/Z1 -> Z3 Admin | Strict separation of deploy admin from Z1/Z0; `deploy_admin_separation != "SEPARATE_ADMIN_AND_AUDITOR"` or `cloud_admin_overlap != "NONE_CLAIMED"` fails with `HOLD_CLOUD_DEPLOY_ADMIN_OVERLAP`. | `DESIGNED` / `SIMULATED_ONLY` (Live: `UNPROVEN`) |
| `T03` | **Long-Lived Bearer Token Returned to Z1/Z2:** Broker mints or returns an OAuth bearer token or API key to Z1/Z2 instead of executing the provider call itself. | Z3 -> Z1/Z2 | Broker must execute provider call directly; `bearer_returned_to_caller=True` or non-broker custody fails with `HOLD_LONG_LIVED_BEARER_OR_KEY_ONLY`. | `DESIGNED` / `SIMULATED_ONLY` |
| `T04` | **KMS or Worker Secret Binding Without Full Policy:** Attacker claims "secrets are in Cloud KMS / Worker Secret Binding" while broker skips directive, media, rights, or spend checks. | Z3 Policy | Secret storage alone is never transaction policy; `secret_binding_mode != "FULL_POLICY_MEDIATED_BY_BROKER"` or `architecture_pattern == "KMS_OR_SECRET_BINDING_ONLY"` fails closed. | `DESIGNED` / `SIMULATED_ONLY` |
| `T05` | **WebAuthn UV Without Independent WYSIWYS Display:** Attacker uses a local passkey/FIDO2 `uv=true` assertion while Z1 substitutes the displayed payload or omits media/account/spend details. | Human -> Z3 | `human_approval_mode != "INDEPENDENT_WYSIWYS_DEVICE"`, `wysiwys_display_mode != "INDEPENDENT_TRUSTED_DEVICE_DISPLAY"`, or `display_sha256 != transaction_sha256` fails with `HOLD_UNTRUSTED_HUMAN_DISPLAY`. | `DESIGNED` / `SIMULATED_ONLY` |
| `T06` | **Post-Consent Media / Account / Target Substitution:** Z1 swaps `final_media_sha256`, `provider_ref`, `account_ref`, `surface`, `action`, or `semantic_target_ref` after Human approval. | Z1/Z2 -> Z3 | Exact digest and field comparison against expected transaction scope; any mismatch fails with `HOLD_ROLLBACK_OR_SCOPE_TAMPER` / `SCOPE_MISMATCH`. | `DESIGNED` / `SIMULATED_ONLY` |
| `T07` | **Direct Nonce or Idempotency Key Replay:** Attacker resubmits a previously used `nonce` or `idempotency_key` to trigger duplicate publication or spend. | Z1/Z2 -> Z3 | Durable outside-SID check against `used_nonces` and `used_requests`; fails with `HOLD_REPLAY_OR_IDEMPOTENCY_UNPROVEN`. | `DESIGNED` / `SIMULATED_ONLY` (Live store: `UNPROVEN`) |
| `T08` | **Semantic Target Rekeying Attack:** Attacker generates a fresh `nonce` and fresh `idempotency_key` (or changes `journey_id`/`expires_at`) for an already-reserved `semantic_target_sha256`. | Z1/Z2 -> Z3 | `semantic_target_digest` covers `(provider_ref, account_ref, surface, action, semantic_target_ref)` independent of nonce/request key; presence in `used_targets` fails with `HOLD_REPLAY_OR_IDEMPOTENCY_UNPROVEN`. | `DESIGNED` / `SIMULATED_ONLY` |
| `T09` | **Ephemeral or Local-Worktree Replay Store:** Broker stores replay state in memory or inside the Z1/Z2 git worktree where crash/reset erases history. | Z3 State | `replay_storage_mode != "DURABLE_ATOMIC_OUTSIDE_SID"` or `replay_store_state == "ABSENT"` fails with `HOLD_REPLAY_OR_IDEMPOTENCY_UNPROVEN`. | `DESIGNED` / `SIMULATED_ONLY` |
| `T10` | **Revocation Epoch Rollback / Old Grant Reuse:** Attacker replays a grant from `revocation_epoch < minimum_epoch` after key rotation or incident revocation. | Z1/Z2 -> Z3 | Monotonic `minimum_epoch` check; any draft with `revocation_epoch < minimum_epoch` fails with `HOLD_REVOKE_LATENCY_OR_EPOCH`. | `DESIGNED` / `SIMULATED_ONLY` |
| `T11` | **Emergency `KILL` / Unknown Revocation Latency:** Compromise occurs during an active window, or broker cannot bound revocation SLA (`revoke_kill_sla_seconds <= 0` or `> 300`). | Z3 Control | Active `incident_kill`, `enrollment_state == "REVOKED"`, or unbounded SLA fails immediately with `HOLD_REVOKE_LATENCY_OR_EPOCH`. | `DESIGNED` / `SIMULATED_ONLY` |
| `T12` | **`UNKNOWN_EFFECT` Retry / Rekey / Budget Release:** Provider call times out or returns unverified status; caller attempts to retry, mint a new `idempotency_key`, or free reserved budget. | Z2/Z3 -> Provider | Any `effect_observation != "NONE"` or `effect_status != "NONE"` locks original `idempotency_key` and `reservation_ref` and fails with `HOLD_UNKNOWN_EFFECT_RETAINED`. | `DESIGNED` / `SIMULATED_ONLY` |
| `T13` | **Unreserved or Over-Ceiling Spend:** Request omits `spend_grant_ref`/`sha256` when `spend_ceiling_minor > 0`, sets `spend_minor > spend_ceiling_minor`, mismatches currency, or uses post-call estimation. | Z3 Budget | Validated at intake (`INVALID_BROKER_SPEND`) and readiness (`HOLD_BUDGET_RESERVATION_UNPROVEN` / `HOLD_ROLLBACK_OR_SCOPE_TAMPER`). | `DESIGNED` / `SIMULATED_ONLY` |
| `T14` | **Stale or Missing Separate Permission Layers:** Caller supplies Human consent but lacks or mismatches `brain_decision`, `provider_entitlement`, `asset_rights`, or `source_sha`/`directive_version`. | Z2 -> Z3 | Each layer has a distinct ref and SHA-256 pin; any missing, stale, or mismatched pin fails with `HOLD_ROLLBACK_OR_SCOPE_TAMPER` or `INVALID_BROKER_PIN`. | `DESIGNED` / `SIMULATED_ONLY` |
| `T15` | **Broker Self-Certification via JSON Object:** Input payload claims `root_enrollment_mode="SELF_ASSERTED_OBJECT"`, `auditor_independence="BROKER_SELF_CERTIFIED"`, or `claimed_statuses={"isolated": True, ...}`. | Z1/Z3 -> Gate | Broker cannot certify itself; all `claimed_statuses` are forced to `UNPROVEN`, and self-asserted root/auditor fails with `HOLD_SELF_CERTIFIED_OR_UNPROVEN_AUDITOR`. | `DESIGNED` / `SIMULATED_ONLY` |
| `T16` | **Counterfeit Cloud Vendor Pre-Selection Claim:** Draft claims a vendor (`CLOUDFLARE_CLAIMED`, `AZURE_CLAIMED`, `GCP_CLAIMED`, `DEVICE_CLAIMED`) has already been selected before Human authorization. | Z1 -> Gate | `vendor_neutral_slot != "UNSELECTED_HUMAN_DECISION_REQUIRED"` fails closed with `HOLD_VENDOR_PRE_SELECTED`. | `DESIGNED` / `SIMULATED_ONLY` |
| `T17` | **Cross-Audience or Cross-Role Claim Confusion:** Audit draft (`PULSE_AUDIT_V1`) or runner/admin role (`SAME_SID_RUNNER`, `CLOUD_DEPLOY_ADMIN`, `PUBLIC_GITHUB`) submitted as broker selection or receipt authority. | Z0/Z1 -> Gate | Strict per-kind audience and role validation rejects with `INVALID_BROKER_AUDIENCE` / `INVALID_BROKER_ROLE` (`REJECT_CROSS_AUDIENCE_OR_ROLE`). | `DESIGNED` / `SIMULATED_ONLY` |
| `T18` | **Hostile Prompt Injection / Media False Proof / Unknown Fields:** Attacker injects prompt instructions, `verified=True`, `allow_external=True`, or extra JSON fields into drafts or state snapshots. | Z0/Z1 -> Parser | Strict `closed()` field sets and `"input_trust": "UNTRUSTED"` reject unknown fields or trusted labels with `CLOSED_FIELDS` / `INVALID_UNTRUSTED_LABEL` (`REJECT_STRUCTURE`). | `DESIGNED` / `SIMULATED_ONLY` |
| `T19` | **Secret / Canary / Private Host Path Leakage in Diagnostics:** Input includes a secret canary, bearer token, Windows path (`C:\Users\...`), or account identifier to leak via exceptions or readiness reports. | Parser / Gate Output | All refs/hashes are regex-pinned; exceptions emit finite `INTAKE_CODES` only, and `ReadinessReport` emits fixed field names and `"0" * 64` preview sentinel only. | `DESIGNED` / `SIMULATED_ONLY` |
| `T20` | **JSON Parser Complexity, Duplicate Keys, Non-Finite Numbers & Digest Tamper:** Payload uses duplicate keys, `NaN`/`Infinity`, depth `> 8`, nodes `> 512`, `> 16384` bytes, boolean-as-int, or altered content without updating `draft_sha256`. | Parser | Bounded `decode_json` and `canonical_bytes` reject before evaluation (`JSON_DUPLICATE_KEY`, `JSON_NONFINITE`, `JSON_COMPLEXITY`, `JSON_SIZE`, `BROKER_DIGEST_MISMATCH`). | `DESIGNED` / `SIMULATED_ONLY` |

---

## 4. Independent No-Secret Canary Proof and Out-of-Band Auditor Protocol

To test isolation in a future Human-authorized trial **without exposing real provider credentials**:

1. **Redacted Canary Enrollment:**
   - The Human (not Z1) places a random 256-bit non-secret canary string `C` into the candidate Z3 secret store and records only `canary_redaction_sha256 = SHA256("pulse-canary-v1:" || C)` in the test expectation.
2. **Zero-Echo Invariant:**
   - Z3 may prove knowledge of `C` only to the loopback/mock trial verifier over an out-of-band channel inspected by the independent auditor. Z3 must **never** include `C` in any response body, HTTP header, error message, or log visible to Z1/Z2.
3. **Out-of-Band Auditor Independence:**
   - The auditor must operate under a separate identity and logging boundary from both the Z1 Windows runner and the Z3 runtime/deployment role. Cloud IAM role assignment alone (`CLOUD_IAM_ROLE_ONLY`) or a JSON field inside a Z3 response (`BROKER_SELF_CERTIFIED`) is rejected (`HOLD_SELF_CERTIFIED_OR_UNPROVEN_AUDITOR`).
4. **Inert Source Ceiling:**
   - In this B0-S.3b source cut, even when every structural pin and design field is valid, `evaluate_readiness` returns at most `status="DESIGN_READY_FOR_HUMAN_SELECTION"`, `external_effect_decision="HOLD_PRIVATE_VERIFIER_NOT_ENROLLED"`, `custody_gate_reason="ISSUER_NOT_ENROLLED"`, `evidence_tier="SIMULATED_ONLY"`, and `claim_assessments=(("isolated", "UNPROVEN"), ("keys_safe", "UNPROVEN"), ("human_verified", "UNPROVEN"), ("provider_ready", "UNPROVEN"))`.
