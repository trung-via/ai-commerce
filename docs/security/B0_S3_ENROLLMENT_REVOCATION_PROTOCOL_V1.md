# B0-S.3a enrollment and revocation protocol v1

Status: **S3A_SOURCE_ONLY / ALL_LIVE_TRANSITIONS_UNSUPPORTED**.
`S3B_LIVE_CUSTODY_UNKNOWN` and `PRIVATE_CUSTODY_VERIFIED=UNKNOWN` remain explicit.
Baseline: `7a4403014d72d203a111f87c0176ef500febd2b0`; source authorization
2026-11-05. No device, broker, issuer, signer, provider or service is enrolled.
This is a future protocol specification plus a pure denial model, not a transport,
credential broker, live verifier, state writer or PULSE execution integration.

The [architecture](B0_S3_CUSTODY_ARCHITECTURE_V1.md) carries the decision matrix,
evidence ladder and primary references. [S2](B0_S2_AUTHORITY_PROTOCOL_V1.md)
continues to hold external effects with `HOLD_PRIVATE_VERIFIER_NOT_ENROLLED`.
Existing contracts, journal, native source locks, leases, QA, rights reopening
and operator/recovery behavior remain prerequisites; none is bypassed here.

## Future enrollment states and independent responsibilities

The table is normative future design. **Every actual live transition below is
presently UNSUPPORTED.** S3a accepts state names only as untrusted diagnostic
observations; it neither executes transitions nor emits ENROLLED/accepted grants.
The verifier and auditor must be separately controlled from Z1 and from each
other's self-assertions. A missing independent audit is UNKNOWN, not verified.

| From -> proposed state | Independent verifier obligation | Separate audit obligation / failure result |
|---|---|---|
| UNENROLLED -> ENROLLMENT_PENDING | Human separately selects device/broker, recovery policy, issuer scope and policy audience through a channel outside Z1; independently pin root ownership and broker identity | Record selection and independent root-admission provenance; no repo JSON, GitHub owner comment or runner configuration can supply issuer proof. Missing proof stays UNENROLLED/HOLD. |
| ENROLLMENT_PENDING -> CHALLENGED | Independent verifier generates a fresh unpredictable nonce with bounded TTL, epoch and audience; bind candidate digest, exact transaction digest, proposed key and independent display | Verify challenge generation/control and single-use reservation outside same-SID worktree; missing or reused challenge HOLD. Draft nonce syntax is not entropy evidence. |
| CHALLENGED -> HUMAN_VERIFIED | Independently authenticate the Human at the enrolled verifier; display full WYSIWYS and bind consent to exactly that challenge/digest/audience/TTL | Confirm display cannot be substituted by Z1, transaction details are complete and cancellation/expiry is visible. WebAuthn UV alone is insufficient. Stale/mismatched consent HOLD. |
| HUMAN_VERIFIED -> ISSUER_PROVEN | Independently verify proof of possession of the proposed issuer key against the admitted out-of-band root and intended key usage/algorithm; verify separate Brain provenance and Human proof | Attest control and private-custody separation with independent challenge evidence. A correct signature alone cannot prove issuer entitlement, custody or full consent. Unproved identity HOLD. |
| ISSUER_PROVEN -> ENROLLED | Commit independent enrollment/root/audience/scope/epoch records, revocation route and durable replay/budget policy atomically; require isolation and revoke/KILL attestation | Auditor binds exact deployment/policy/root/epoch, actual test receipts and limits; source PASS or current chat is not signed strategy provenance. Failure remains HOLD/UNKNOWN. |
| Any nonterminal or ENROLLED -> REVOKED | Independently authenticated kill/withdrawal/compromise/rotation advances monotonic epoch; stop new reservations/submissions and invalidate covered grants/keys | Audit enforcement at every effect boundary, acknowledgement and provider session revocation; preserve in-flight/UNKNOWN identities and exposure. Missing acknowledgement UNKNOWN. |
| Pending/challenge/Human/issuer/enrolled -> EXPIRED | Enforce authenticated time and exact TTL at use time; remove ability to begin new effects under expired proof | Audit expiry at boundaries and outstanding reservations. Expiry does not prove NO_EFFECT or release uncertain spend. |
| Any -> UNKNOWN | Root, time, epoch, display, journal, replay, provider outcome or independent evidence missing/conflicting/rolled back | Stop effects; record uncertainty without a positive transition or local repair that erases history. |
| REVOKED / EXPIRED / UNKNOWN -> ENROLLMENT_PENDING | A separately authorized new out-of-band enrollment ceremony; retain old tombstones, original effect keys and all unresolved reservations | Fresh root/epoch and compromise/reconciliation evidence required; no direct restoration to ENROLLED and no auto-rotation/retry. |

An enrollment challenge can be used to demonstrate possession, but future
enrollment cannot itself grant any provider transaction. Each later operation
requires its own independent fresh challenge, complete consent and final policy
check. Structural `candidate_sha256`, `display_sha256` or key labels prove none
of these ceremonies. S3a does not implement a Human or Brain identity attestor.

## Transaction binding and independent permission layers

| Covered fields / proof | Future verifier requirement |
|---|---|
| Protocol kind/version and policy audience | Reject cross-protocol, wrong verifier/audience, wrong use or stale policy; never accept a proof only because its key ID is known. |
| Issuer reference, exact public key ID, admitted root, algorithm and revocation epoch | Independently admit root/key ownership and usage; verify possession, signature, current epoch and revocation outside Z1. Key IDs contain no private material. Algorithm negotiation/signatures are deliberately absent from these drafts. |
| Journey, source SHA, directive version and digest | Bind exact reviewed source and strategy version; no upgrade of a source Brain PASS, public comment or copied BrainDecision into private provenance. |
| Final media SHA-256 and exact logical target | Independently display/review the final media and other significant content (including caption/destination). The content digest must cover all executed bytes/parameters, not a mutable URL or preview thumbnail. Any change requires new review. |
| Provider, account, surface and action | Bind exact opaque identities to independently resolved private target details; verify actual provider capability/entitlement separately from Human consent. |
| Brain decision reference/digest | Independently authenticated strategy evidence for this exact directive/action/final media; never a provider or spend grant. |
| Human grant reference/digest | Independently authenticated full WYSIWYS consent, fresh challenge and exact transaction; never inferred from the current source-work authorization. |
| Asset rights reference/digest | Reopen fresh action-specific owner evidence for media/data/claims/privacy/voice/music; stale/revoked rights HOLD. |
| Provider entitlement reference/digest | Independently establish actual account/surface/action availability and policy at effect time; labels alone HOLD. |
| Currency, spend amount/ceiling and separate spend grant reference/digest | Nonnegative exact integer minor units; amount <= ceiling, reserve the maximum authorized exposure atomically. Zero spend is explicit; positive ceiling requires separate grant. Syntax proves no balance or entitlement. |
| Nonce, stable idempotency key, issued/expiry times, epoch | Fresh independently generated challenge, single use, half-open TTL and monotonic revocation check immediately before effect. Client clock and repo epoch are untrusted. |
| Stable semantic target and canonical transaction digest | Reject replay/rekey across nonce, journey, directive/source revision, media substitution or changed currency/budget. Independent mapping must detect target aliases; a caller can recompute every hash without gaining authority. |

Future full WYSIWYS shows the actual final media/content, destination identity,
provider/account/surface/action, source/journey/directive version, rights and
entitlement, currency/ceiling, TTL, cancellation limits and original effect
identity in an independent trusted display. Consent must be bound to the exact
digest subsequently checked by Z3 immediately before one effect. Authentication
and consent are separate proofs even if one interface collects them. A passkey
may contribute verifier-bound authentication; it cannot alone prove complete
transaction consent, provider entitlement, spend or token custody.

## Durable replay, reservation and uncertain outcomes

Only a future Z3 can own the durable replay register, enrollment ledger, monotonic
epoch anchor, semantic-target index and budget reservations. All must be outside
the same-SID engineering worktree/control plane. A local database, checksums,
TPM label, HMAC fixture or copied snapshot is not independent anti-rollback proof.

1. Validate independent identities, exact transaction/consent, fresh rights and
   entitlement, time/audience/epoch and native PULSE gates. Reserve nonce,
   idempotency key, stable logical target and maximum currency exposure atomically
   in Z3 before any provider call. Detect aliases and cross-journey rekeying.
2. Recheck kill/revocation/TTL and exact content at the effect boundary; one
   reservation permits at most the separately authorized attempt. Do not export
   a bearer credential to Z1/Z2. Native fencing must remain effective against a
   stale worker; a journal transaction cannot atomically commit a provider call.
3. If submission might have happened, retain **UNKNOWN_EFFECT**, original
   idempotency key, target and reservation. Never auto-retry, allocate a fresh
   key/nonce to escape UNKNOWN, release the budget, or infer a refund from timeout,
   cancellation request, restart, expiry, key rotation or local file deletion.
4. Independently authenticate the provider receipt for the original request/key,
   target, outcome and actual currency/cost. Only verified confirmation supports
   confirmation. A supplied `CONFIRMED`, successful HTTP response or local digest
   is insufficient; no such receipt verifier is implemented here.
5. Reconcile the original identity using independently authorized observation.
   Proven NO_EFFECT still leads to HOLD and a separately reviewed attempt/grant,
   never automatic retry. Uncertain observation remains UNKNOWN with reservation
   held. Reference native semantic-owner receipts without duplicating commerce truth.

The pure model's `used_nonces`, `used_requests`, `used_targets`, epoch and coherent
snapshot are injected comparison data only. Diagnostic denials mutate nothing.
Even `CLAIMED_CONFIRMED` and `CLAIMED_NO_EFFECT` stay UNKNOWN_EFFECT; they cannot
release exposure. No positive authority API or executable broker exists.

## Closed draft and model contract

`CustodyEnrollmentCandidate.v1`, `CustodyChallengeDraft.v1`,
`RevocationNoticeDraft.v1`, and `BrokerTransactionDraft.v1` are closed independent
drafts in [custody_claims.py](../../src/ai_commerce/production_runtime/custody_claims.py).
They change no B0.1–B0.4/S1/S2 schema. They reuse bounded S2 canonical/decode
utilities: <=16,384 bytes, depth 8, 512 nodes, <=64 entries/object or array and
1,024 characters/string; reject duplicate keys, nonfinite numbers, cycles,
custom types, excessive integers, malformed Unicode and missing/unknown fields.
Canonical sorted ASCII compact JSON and SHA-256 identify content only; no
RFC canonicalization, signing or trusted test root is claimed.

Refs use an exact namespace plus 64 lowercase hex; source SHA is 40 lowercase
hex, content hashes 64. Public key IDs are exactly `key:` + 64 hex and issuer
references exactly `issuer:` + 64 hex; neither contains an encoded key. The
closed audiences are `PULSE_CUSTODY_V1` and `PULSE_AUDIT_V1`; the latter is a
cross-audience negative, never effect authority. Times are caller-supplied
integer Unix UTC seconds; positive windows <=900 seconds, challenges <=120.
Epochs and directive versions are exact positive 31-bit integers; Boolean numbers
reject. Revocation notices advance `previous_epoch` and cannot undo a tombstone:
their TTL bounds delivery freshness, not how long revocation remains effective.

All `untrusted_labels.signature/attestation/verified/enrolled/key` values are
untrusted claims, including true/CLAIMED. No actual signature or secret bytes
are accepted; unknown fields reject. The separate evidence snapshot is closed
and bounded, revalidated on each call, and has no trusted configuration/callback.
Any direct `CustodyDraft` construction is revalidated by the gate.

`semantic_target_sha256` covers stable target/provider/account/surface/action;
it deliberately excludes caller rekeys, journey, media, grants, spend and TTL.
Target alias resistance needs the future independent register, not this hash.
`transaction_sha256` covers every transaction field except itself. Challenge
digest/display equality is only structural comparison, never independent display
evidence. Every label and hash can be attacker-generated.

[custody_gate.py](../../src/ai_commerce/production_runtime/custody_gate.py) has only
PREVIEW_INERT, HOLD, REJECT outcomes and finite data-free reasons. `decide` always
denies; a crafted all-gates-true snapshot ends ISSUER_NOT_ENROLLED. The separate
preview returns fixed field names and a fixed all-zero hash sentinel for Human
reading; it emits no transaction values, actual hashes, consent or permission.
It is not a general redaction/DLP or authenticated display implementation.

## Future revocation, rotation, kill and redaction runbook

All live steps require separate Human authorization and real independent
execution; this source cut performs none of them.

1. **Suspected compromise:** stop new effects at independently enforced KILL;
   identify affected public refs/epochs without reading or publishing secrets.
   Preserve original in-flight keys, target mapping and reservations.
2. **Revoke:** use the independent channel to tombstone affected issuer/grants,
   advance epoch, revoke provider sessions/tokens and require acknowledgements.
   Unknown revocation reachability means HOLD, never “revoked successfully”.
3. **Rotate:** independently generate/admit replacement ownership outside Z1,
   verify possession, new root/key usage/epoch and recovery policy; retain old
   revocation records. No automatic re-enrollment or resurrection by repo edit.
4. **Reconcile:** independently observe provider outcomes using original keys;
   confirm actual costs/cancellation/refunds before exposure changes. Uncertain
   or unavailable receipts remain UNKNOWN and cannot restart an effect.
5. **Restore:** independent auditor verifies isolation, stolen-proof rejection,
   old-key/epoch rejection, kill/revoke latency, rollback handling and receipt
   semantics on the separately selected real deployment. Obtain fresh exact
   transaction approval; B0-S.4 live tests need separate scope.
6. **Redact/export:** allow only reviewed source hashes, finite reasons, aggregate
   test/worker counts and deliberately public opaque references. Exclude raw
   account/customer/provider records, prompts/chat, media, key/token/signature
   material, private paths, environment/argv dumps and raw receipts. Digests can
   correlate private activity; review them too. If exposure occurs, remove public
   copies where possible and independently revoke/rotate; redaction is not
   revocation. No secret examples or automatic export are provided here.
