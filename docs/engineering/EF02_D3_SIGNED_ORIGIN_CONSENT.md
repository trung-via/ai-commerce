# EF02-D3.2 — Operator-signed Origin Consent

This package is a bounded local `OriginAuthority` implementation for the independent AI Commerce project, tracked in [Issue #109](https://github.com/trung-via/ai-commerce/issues/109). **No ChatGPT message is posted. No automatic Wake, receipt or native Brain continuation is claimed.**

## Trust model

The D2 `LocalCallerAttestation` (including `is_local`, `attestation_verified`, `caller_id`, `auth_token`) is a caller-provided **hint**, not authority. `SignedOriginConsentAuthority` authorizes only when **all** checks succeed:

1. **Operator-enrolled, immutable signed grant** with explicit `human_consent=true`, strict operation, exact scope, expiry and registry digest. Signed grants come only from the trusted local application/configuration, never from an event, Issue or browser tab.
2. **Independent identity observation:** `principal_provider()` must return the operating-system-authenticated identity of the actual local service/caller, provisioned by the operator. Merely reflecting `attestation.caller_id` or an untrusted request field is invalid.
3. **Operator-owned HMAC key:** `secret_provider()` must return at least 32 bytes of secret material held outside the repo/logs/issues. The authority validates a SHA-256 HMAC over a canonical JSON object, including identity, operation and consent. A public hash, token equality or `attestation_verified=True` cannot mint a grant.
4. **Registry integrity:** authorization on an existing D2 transaction compares its validated `registry` snapshot to a grant's `registry_sha256`. The final check occurs *inside the D2 registry lock* before a mutation or adapter call. Registry changes invalidate old grants for subsequent actions, requiring an operator-issued refreshed grant.

Grant body keys: `kind="AI_COMMERCE_SIGNED_ORIGIN_CONSENT"`, `version=1`, `caller_id`, `principal`, `operation`, `scope`, `registry_sha256`, `valid_until` (integer epoch seconds), `human_consent=true`. `signature` is the lowercase HMAC-SHA256 of compact sorted-key ASCII JSON of exactly that body. Full signed manifest and chat route stay **outside the checkout**, in operator-controlled private local configuration. Do not serialize HMAC material or consent data into GitHub artifacts or EF02 Issue comments.

Supported exact operations: `register_route`, `bind_claim`, `resolve_claim_route`, `deliver_wake`. Signed `scope` matches the D2 method context **exactly**. `bind_claim` must provide an explicit generation; an implicit “latest generation” cannot be signed. For `deliver_wake`, the signed scope contains the exact validated D1 completion event. There is no newest-tab/another-chat fallback.

## Trust boundary and limitations

- **Grant issuance is external.** This module deliberately does not issue Human consent, create HMAC keys, store the consent manifest, log into the OS, select the chat or provide a default principal provider. It cannot establish independent Human consent if the trusted operator/host has not already done so.
- HMAC authenticates operator-issued grants, not the real-world identity of a user by itself. The operator must provision a key inaccessible to untrusted callers and a principal provider derived independently from a trusted local process/session identity. A callback that returns an unverified caller-supplied string **does not** meet this trust boundary.
- A forged/stale/expired/tampered grant, different principal/caller/operation/route/batch, or changed registry is rejected; without trusted dependencies the authority fails closed. A compromised trusted process/key is outside this scope.
- Signed grants are operator-managed and require explicit refresh as the registry changes. The code cannot claim automatic revocation or secret rotation in an external key store.
- D3.1 authenticates GitHub artifacts independently; D3.2 is *separate* local consent/identity evidence. Both are required for eligible D2 processing. Neither provides a ChatGPT write API or an independent exact-message receipt.
- This code does not bypass, relax or remove D2's locked registry, idempotency checks, UNVERIFIED_ORIGIN handling, UNKNOWN/UNSUPPORTED fallback or Human merge approval.

## Evidence

New tests cover valid signed grants, caller flags without authority, wrong HMAC key, signed payload tampering, expiry, exact-route and operation mismatch, caller/principal mismatch, deep-copy enrollment and actual registrar/bind-claim behavior with registry hash pinning. CI on Windows and Ubuntu is required before Human-approved merge. The real operator-managed grant signing, OS-principal integration and ChatGPT message delivery are **not** exercised by CI.

**Rollback:** revert this module, its import, tests and this document. No global OS, credential store, repository permissions or EF02 scheduler changes.
