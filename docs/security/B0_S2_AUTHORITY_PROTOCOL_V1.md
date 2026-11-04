# B0-S.2 authority protocol v1

Status: **SOURCE_ONLY / OPTION_A / PRIVATE_VERIFIER_NOT_ENROLLED**.
Baseline: `0c9753f873ce8aaf758a691664c684c624a8d305`; Human scope date 2026-11-04.
[Research Issue #356](https://github.com/trung-via/ai-commerce/issues/356),
[pinned PA2 roadmap](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/docs/roadmap/ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md)
and [pinned ADR](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/docs/architecture/ADR_B001_BRAIN_LED_INDEPENDENT_RUNTIME.md)
define the consumer and boundaries. No signer, verifier, provider adapter or
release API is enrolled, deployed or connected in this cut.

## Separate decisions and proofs

| Layer | What is required for a future real effect | What cannot substitute |
|---|---|---|
| Brain strategy | Current-chat review of exact directive/version/final asset and action | A copied JSON actor, BrainDecision draft, public source PASS or owner comment |
| Brain private provenance | Independent enrollment and issuer verification binding the actual current chat/decision bytes | The fact this ChatGPT chat reasoned about a strategy, a screenshot or `verified=true` |
| Human transaction consent | Independently authenticated approval of one exact transaction with target, expiry and revocation | GitHub owner comment, HostOps WRITE, universal delegation or an earlier grant |
| Provider entitlement | Independently checked actual capability/API/account/surface eligibility at use time | Configured adapter, provider label, self-asserted entitlement or Human permission |
| Asset/data rights | Fresh rights/voice/music/input/claim/privacy evidence for the relevant action | Provider account eligibility, synthetic provenance or unrelated rights |
| Spend grant | Separate per-action currency/ceiling reservation and reconciliation if nonzero | GO_PUBLISH, a strategy budget suggestion, a free prior action or accounting estimate |
| External observation | Independently verified provider receipt matching exact request/key/target/outcome | Label PROVIDER_ATTESTED, supplied receipt, local checksum, CI or fabricated confirmation |

No signed claim may be described as **valid authority** without an independent,
enrolled trust root and complete issuer/field/revocation verification. A correct
signature calculation alone would still not establish issuer entitlement or
Human intent. This implementation performs no signature calculation at all.

## Present finite policy

The independent draft names are `OfflineOperationIntent.v1` and
`ExternalEffectIntentDraft.v1`. Existing CreativeDirective, BrainDecision and
ProductJourneyReceipt schemas remain frozen. Structural success is explicitly
**not issuance, authentication or permission**. There is no external-grant
context, signer callback, trusted boolean, network/env/secret IO, mutable global
authority, persistent replay store or arbitrary plugin import in these modules.

| Input | Result | State/effect consequence |
|---|---|---|
| Invalid bounded JSON, unknown domain/action or closed-field violation on local request | REJECT with finite reason | No transition or effect |
| Recognized external action, including malformed/forged authority fields | HOLD_PRIVATE_VERIFIER_NOT_ENROLLED | No release edge, even under mocked all-grants-true input |
| Local intent without matching revalidated native scope | HOLD_NATIVE_SCOPE / HOLD_SCOPE_MISMATCH | No native gate bypass |
| Local marker corrupt/rollback/UNKNOWN, native unsafe state, duplicate/rekey comparison | HOLD_RECOVERY / HOLD_REPLAY | Preserve original journal/effect identity; no retry |
| Local rights stale/expired/mismatched | HOLD_LOCAL_RIGHTS | No affected operation |
| PLAN_OFFLINE | HOLD_NATIVE_ACTION_NOT_ALLOWED | B0.3 does not admit this action |
| RENDER_LOCAL_OFFLINE | ALLOW_LOCAL model result only from INPUT_VALIDATED | Reviewed B0.3 A06 still-image tool, unchanged native source pin and parameters; actual native checks still required |
| READ_STATUS | ALLOW_LOCAL model result in INPUT_VALIDATED/RUNNING/TECHNICAL_QA/WAIT_BRAIN | No permission to mutate journal or perform remote status |
| EXPORT_DRAFT / HANDOFF | ALLOW_LOCAL model result only at WAIT_BRAIN | Technical QA/native handoff checks remain required; manual information transfer only |

All local results require explicit HUMAN_ATTENDED, LOCAL_UNPAID, exact native
source/journey/directive-version/digest and selected asset, nonempty opaque
nonce/request/rights pins, zero spend, currency NONE and fixed local target
labels. B0.3's existing source lock is
`32f42b84567e1217a93854389b1c96435bbf454d`; it is distinct from this engineering
baseline. The model preserves that lock, not silently upgrading the worker.
No new policy is wired into B0.1–B0.4 execution. Supplied coherent scope/marker
bytes are model inputs, not live journal, source authenticity or custody proof.

Remote GENERATE_MEDIA, POST/PUBLISH/GO_PUBLISH, GO_SPEND, IMPORT_REMOTE,
IMPORT_ANALYTICS, NETWORK/UPLOAD and FINANCIAL/PURCHASE/TRANSFER/SETTLE always HOLD.
An unknown effect name rejects; relabeling a known external action LOCAL_UNPAID
still HOLDs. No positive live release function exists.

### Canonical draft and logical time rules

Raw UTF-8 JSON is at most 16,384 bytes, depth 8, 512 nodes, 64 entries per object
or array and 1,024 characters per string. Duplicate keys, nonfinite numbers,
oversize integers, invalid Unicode, custom Python objects, cycles, unknown or
missing fields reject. Parsing detaches input before use. Canonical draft bytes
are sorted ASCII JSON with compact separators and no NaN; SHA-256 identifies
content, not issuer. This is not a claim of RFC 8785 or RFC 9421 implementation.

Source SHA is exactly 40 lowercase hex; all content digests are 64 lowercase hex.
Opaque draft refs are fixed namespace + 64 hex; they never contain a path, URL,
prompt, account name or credential. Provider/account/surface/currency are required
and nonempty for external drafts. Positive ceilings require a spend ref, a
three-letter currency and `0 <= spend_minor <= spend_ceiling_minor`; Boolean
numbers are rejected. Structural currency formatting proves no entitlement,
exchange-rate knowledge, budget balance or grant. All external drafts still HOLD.

Times are integer UTC Unix seconds supplied as `at`, `issued_at`, `expires_at`.
Require `issued_at <= at < expires_at` and a maximum 900-second request window;
local rights/provider-policy expiry is also checked. No wall clock is consulted.
Clock truth, drift resistance and issuer freshness require future independent
verification. Tests supply synthetic logical time and comparison snapshots only.

## Future enrollment and transaction finite state requirements

The following is a **design obligation, not implemented states or an API**.
Today every path toward ENROLLED ends at HOLD_PRIVATE_VERIFIER_NOT_ENROLLED.

| From -> proposed state | Required independent checks | On failure |
|---|---|---|
| UNENROLLED -> ENROLLMENT_PENDING | Separate Human choice of custody; issuer/key ownership challenge outside Z1/same SID; authenticate audience and current-chat provenance mechanism | Remain UNENROLLED |
| ENROLLMENT_PENDING -> ENROLLED | Independently controlled trust-root admission, key ID/algorithm/scope, epoch, revocation channel and recovery policy; audited proof of possession | HOLD; never trust repo signer JSON |
| ENROLLED -> CHALLENGED | Verifier-generated unique nonce, audience and exact transaction digest; atomic independent replay register reservation | HOLD on missing/duplicate/rollback |
| CHALLENGED -> REVIEWED | Independent WYSIWYS display of exact final transaction; distinct Brain provenance and Human consent proofs | HOLD on substitution or ambiguous intent |
| REVIEWED -> GRANTS_CHECKED | Fresh Brain/Human/provider/rights proofs; source/final asset/account/surface/action and revocation pins; separate spend cap/currency | HOLD on unknown/inapplicable self-assertion |
| GRANTS_CHECKED -> RESERVED | Durable nonce/key consumption + cross-journey semantic target and currency budget reservation, native current lease/fence/QA | HOLD on duplicate/rekey/stale generation |
| RESERVED -> SUBMITTED | Final independently enforced adapter check of exact signed bytes immediately before one authorized call | CANCELLED before call, or UNKNOWN if submission may have happened |
| SUBMITTED -> CONFIRMED | Authenticated external receipt binds original request/provider key, final target, actual outcome and cost | UNKNOWN_EFFECT on missing/ambiguous receipt |
| UNKNOWN_EFFECT -> RECONCILED | Independently query original provider token/key; preserve reservation and exposure | No replay/new key; UNKNOWN remains blocked |
| Any pre-submit state -> CANCELLED/EXPIRED/REVOKED | Actual independent cancellation, TTL or issuer epoch check | No effect; old proof cannot be rekeyed |
| Submitted/unknown -> cancellation requested | Preserve original intent; verify provider's actual cancellation/result | Cancellation request alone never means no effect or refund |

Future wire coverage must include `kind/policy_version`, enrolled issuer/key ID,
algorithm and verifier audience, current-chat/Brain decision ref + digest,
Human grant ref + digest, source SHA, journey, directive version/digest, exact
final asset hash, provider/account/surface/action, rights refs/digests and scope,
provider entitlement refs/digests, nonce, issued/expiry time, revocation epoch,
stable idempotency/request key, transaction digest, currency and ceiling, and
separate spend-grant digest when applicable. External receipts additionally bind
provider request ID, outcome, observed amount/currency and receipt proof/ref/hash.
Changing any covered field invalidates the prior transaction proof and requires
fresh review. A new key must not evade an already reserved semantic transaction.

The nonce/replay register, root/enrollment ledger, monotonic revocation epoch and
budget reservations must be independent of the public runner and a compromised
same-SID local journal. Key rotation must revoke prior epochs as designed;
signature math on an old key cannot override revocation. Deletion/replacement of
local files is not cancellation, anti-rollback or refund evidence. UNKNOWN holds
exposure until independent reconciliation; proven NO_EFFECT leads to HOLD and a
separately reviewed attempt, not automatic retry. Existing B0.2/B0.3 recovery,
leases and B0.4 QA/admission remain mandatory even after future enrollment.

## Transaction WYSIWYS

What-you-see-is-what-you-sign means the Human's independent confirmation shows
the exact final media/version/hash, caption and destination account/surface,
provider/action, source/directive/journey, rights/entitlement scope, currency,
maximum charge, expiry and cancellation terms bound in the canonical transaction.
Do not display a harmless preview while authorizing different bytes or a broader
action. The future independent verifier compares that digest again at the effect
boundary, consumes its unique challenge once, and records actual external outcome.
Brain strategy review, Human release and optional spend remain distinct even if
one interface presents them together. A consumer ChatGPT session is not assumed
to have a signing key or an available private identity attestor.

## Reversible choices

| Option | Present decision | Required additional evidence |
|---|---|---|
| A: attended offline drafts only | **NOW**; no credentials, service, provider, spend or posting; sanitized manual same-chat handoff is information only | Source tests, owner/consumer closure, independent exact-source Brain review |
| B: attended out-of-band signer / isolated broker | Future Human selection; independent issuer/verifier and provider custody outside public runner/same SID | B0-S.3 enrollment, possession, revocation and isolation proof; B0-S.4 separately authorized tamper/recovery proof; actual action-specific entitlements |
| C: always-on service/broker | Deferred; no deployment authorization here | B proof plus operations, recovery, expiry/revocation, budget and real provider receipts for each scope |

No fake cryptographic positive production tests are acceptable. Source fixtures
prove parser/model behavior and denial, never actual same-SID adversarial defense,
private Human/Brain identity, key custody or provider authorization.

## External primary references (background only)

References were read for source design. None is an implemented AI Commerce trust
root, transport choice, certification or live security receipt.

- [GitHub self-hosted runner security](https://docs.github.com/en/actions/reference/security/secure-use#hardening-for-self-hosted-runners): public engineering execution may persist compromise; do not place private production keys on that runner.
- [Microsoft CryptProtectData / DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata): protection normally follows the user's logon credential. Our inference is that it does not isolate malicious code under the same SID.
- [NIST SP 800-207](https://csrc.nist.gov/pubs/sp/800/207/final): resource access requires explicit policy decisions rather than trust inferred from network location; motivates separating public input from private enforcement.
- [NIST SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b.html): authenticator/verifier binding, replay resistance and authentication intent inform future independent enrollment and challenges; no conformance claim.
- [OWASP Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html): deny by default and check authorization per request; a role label alone is insufficient.
- [OWASP Transaction Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Transaction_Authorization_Cheat_Sheet.html): bind confirmation to significant transaction details, uniqueness and expiry; informs future WYSIWYS.
- [RFC 9421](https://www.rfc-editor.org/rfc/rfc9421.html): covered message components, freshness, nonce and verifier trust are background for a future transport review. No signing/verifying or RFC-positive production test is added.

See [inventory](B0_S1_TRUST_ZONE_INVENTORY_V1.md) for exposure/redaction and
[handoff](../engineering/B0_S1_S2_SOURCE_HANDOFF.md) for actual verification and
remaining S.3/S.4/independent-review gates.
