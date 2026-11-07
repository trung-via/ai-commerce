# Executor health private bridge v1

**SOURCE_ONLY_NOT_DEPLOYED — Phase 2A source contract.**

This module supplies a typed Python request/response adapter around the existing
`collect_executor_health_local()` in `src/ai_commerce/hostops_v3/personal.py`.
It installs no transport, endpoint, connector, CLI operation, scheduled task or
listener. This plugin cannot remotely operate Windows or inspect actual private
account quota without a separately installed, authenticated transport. Merging
this source does not deploy a private bridge or complete account quota integration.

## Admission and trust

`ExecutorHealthPrivateBridge(origin_id=..., chat_id=..., authorize=..., clock=...)`
is constructed by trusted local code. `read(raw: bytes)` accepts at most 4096
UTF-8 JSON bytes. The constructor pins the exact authenticated origin and current
chat using opaque lowercase SHA-256 identifiers; these identifiers are bindings,
not credentials, and must not contain raw account identity. No request can choose
the pins, authorization verifier, clock, collector, executable or evidence source.

The external `authorize(canonical_request_bytes)` callback must independently
verify authenticated operator/connector authority, private-read permission and
binding of **every** request field. Canonical bytes are ASCII JSON with sorted
keys, compact separators, `ensure_ascii=True`, and no trailing newline. A digest,
JSON `authenticated` flag, possession of origin identifiers, model prose, login
status or a source receipt alone cannot establish authority. Missing trust returns
`UNAVAILABLE/TRUST_UNAVAILABLE`; an exception or anything other than literal
`True` from the verifier returns `UNAVAILABLE/AUTHORIZATION_UNPROVEN`. No collector
or CLI runs in those cases. Test verifiers are stubs, not installed authentication.

## Closed request: `ExecutorHealthPrivateRequest.v1`

Every key below is mandatory; extra keys and duplicate JSON keys are rejected.
Integers exclude booleans, floats and strings. Time is real UTC in the exact
`YYYY-MM-DDTHH:MM:SSZ` format; offsets, fractions and naive time are rejected.

| Field | Exact value or bound |
| --- | --- |
| `schema_version` | `ExecutorHealthPrivateRequest.v1` |
| `operation` | `executor_health_snapshot` |
| `visibility` | `LOCAL_PRIVATE` |
| `origin_id` | 64 lowercase hex characters, equal to the trusted origin pin |
| `chat_id` | 64 lowercase hex characters, equal to the trusted current-chat pin |
| `nonce` | 32 lowercase hex characters; future transport supplies a random 128-bit nonce |
| `sequence` | Integer 1 through 9007199254740991, strictly increasing in the trust session |
| `issued_at` | Exact UTC time, nondecreasing across admitted requests |
| `ttl_seconds` | Integer 1 through 60; expiry is exclusive |
| `max_age_seconds` | Integer 1 through 60; snapshot expiry is exclusive |
| `providers` | Exactly `["codex","antigravity"]`, in that order |
| `requested_fields` | Exactly `["preferred","fallback","executors"]`, in that order |

The provider and field lists describe one fixed complete snapshot. Subsets,
duplicates, reordered lists, unknown providers, quota selectors, account selectors,
commands, shell fragments, paths, executable names and arbitrary probe arguments
are rejected. There is no command interpolation or dynamic operation dispatch.
No EF01 report or trust verifier can be supplied through this request.

Structural, origin, future, stale and replay rejection happens **before** the
collector, discovery, policy read or any CLI subprocess. Invalid requests raise
`Denied` with a fixed code: `PRIVATE_HEALTH_REQUEST_INVALID`,
`DUPLICATE_JSON_KEY`, `PRIVATE_HEALTH_ORIGIN_DENIED`,
`PRIVATE_HEALTH_REQUEST_TIME_DENIED` or `PRIVATE_HEALTH_REPLAY_DENIED`.
Invalid constructor pins and host clocks use `PRIVATE_HEALTH_ORIGIN_INVALID`
and `PRIVATE_HEALTH_TIME_INVALID`. Untrusted input text is never used as an error.

## Replay and freshness

One adapter instance serializes admission and collection using a nonblocking lock.
Concurrent/reentrant reads return `UNAVAILABLE/BUSY`. The instance remembers every
admitted nonce, the highest sequence, latest issued time and host clock. A reused
nonce, equal/lower sequence, backward issued time or backward host clock is denied.
The clock and TTL are rechecked after authorization and before collection. A nonce
and sequence are consumed before collection, including when collection fails or
the resulting snapshot expires. There is no automatic retry.

Memory is capped at 4096 admitted nonces, with no eviction; capacity returns
`UNAVAILABLE/REPLAY_CAPACITY`. This is an **in-memory source contract**, not durable
replay protection. Creating another instance or restarting loses that state. A
future transport must keep one instance per authenticated session and supply
durable atomic replay protection across restarts and multiple processes, or rotate
to a newly authenticated session/origin binding that cannot accept old requests.
Reconstructing an instance per request is not a valid deployment.

The local collector runs at most once for an admitted request. Its observation
time must equal the admitted host time rounded down to seconds. Completion is
checked against the earlier of request expiry and observation plus maximum age.
Expired, future, mismatched or clock-regressed observations return
`UNKNOWN/SNAPSHOT_NOT_READY` with no snapshot. Non-Windows or invalid/unavailable
source returns `UNAVAILABLE/SOURCE_UNAVAILABLE`, with no private exception text.
An absent executable can still be reported as an observed `ABSENT` state.

## Closed response: `ExecutorHealthPrivateResponse.v1`

`validate_executor_health_private_response()` validates all fields and returns a
detached copy. It rejects extra fields rather than forwarding or redacting them.
This validates source structure; it does not verify a signature or authenticate a
remote response. The response has precisely these keys:

| Field | Contract |
| --- | --- |
| `schema_version` | `ExecutorHealthPrivateResponse.v1` |
| `visibility` | `LOCAL_PRIVATE` |
| `capability` | `SOURCE_ONLY_NOT_DEPLOYED` |
| `request_sha256` | SHA-256 of the full canonical request |
| `responded_at` | Exact UTC host response time |
| `valid_until` | Exact UTC exclusive freshness deadline on success; otherwise null |
| `max_age_seconds` | Request's integer maximum age, 1 through 60 |
| `status` | `OBSERVED`, `UNAVAILABLE` or `UNKNOWN` |
| `code` | One of the status/code pairs documented here |
| `freshness` | `FRESH` for `OBSERVED`; otherwise `NOT_READY` |
| `snapshot` | Closed snapshot on success; otherwise null |

`OBSERVED/LOCAL_OBSERVATION_ONLY` establishes a local observation, not verified
account identity, usable capacity, provider health, a live bridge or remote access.
Consumers must bind `request_sha256` to their own authenticated canonical request,
verify signed native readback, and compare their trusted current UTC clock with
both `responded_at` and `valid_until` before use. A structurally valid old response
is not fresh merely because the structure validator accepts it. Responses are
bounded to 16384 ASCII JSON characters.

The snapshot keys are exactly `observed_at`, `windows`, `context_identity`,
`preferred`, `fallback` and `executors`. `windows` must be literal true for an
observed private response; `context_identity` remains `UNKNOWN`. Policy is projected
to closed enums only: preferred is `CODEX_GPT_6_1_SOL_XHIGH` or `UNKNOWN`, fallback
is `ANTIGRAVITY_GEMINI_3_8_FLASH_HIGH` or `UNKNOWN`. Even a syntactically valid
unrecognized source policy becomes `UNKNOWN`; no raw model selector is forwarded.
These enums describe configured preferences and never invoke fallback or recovery.

`executors` contains exactly two ordered rows, `codex` then `antigravity`. Each
row reuses the strict `ExecutorHealthLocalReceipt.v1` row contract and adds only
`percentage: null`:

| Row field | Allowed values |
| --- | --- |
| `executor` | Fixed row identity, `codex` or `antigravity` |
| `executable` | `PRESENT`, `ABSENT`, `UNAVAILABLE`, `UNSUPPORTED_LAUNCHER`, `UNKNOWN` |
| `version_state` | `OBSERVED`, `UNKNOWN`, `TIMEOUT`, `UNAVAILABLE`, `MALFORMED` |
| `version` | Bounded parsed numeric CLI version when observed; otherwise null |
| `authentication` | `LOGIN_REPORTED`, `NOT_LOGGED_IN`, `UNKNOWN` |
| `login_probe` | `OBSERVED`, `UNKNOWN`, `TIMEOUT`, `UNAVAILABLE`, `MALFORMED`, `NOT_SUPPORTED` |
| `provider_identity`, `account`, `quota` | Always `UNKNOWN` |
| `remaining`, `reset_at`, `percentage` | Always null |
| `last_ef01` | Closed `{state,outcome,observed_at}` observation from the existing local contract |

The local validator also enforces cross-field consistency. Antigravity login is
`NOT_SUPPORTED` with authentication `UNKNOWN`. `last_ef01.state` is one of
`UNKNOWN`, `UNTRUSTED`, `INVALID`, `FRESH`, `STALE`; outcome is `UNKNOWN`,
`QUOTA_EXHAUSTED` or `DETERMINATE_RETURN` with the existing trusted age rules.
This adapter supplies no historical report, so its current rows have unknown
EF01 history. Login/presence, historical quota exhaustion and successful execution
do not measure current quota. Numeric remaining quota, reset time, percentages,
raw account identifiers, stdout/stderr, paths and exceptions cannot cross this
contract. Actual quota requires separately authorized private-account verification
and documented provider quota APIs; v1 always reports unknown/null.

## Native and public boundaries

The adapter adds no probes. The existing collector retains only these fixed slots,
each with a five-second timeout, `shell=False`, no stdin and no provider invocation:

1. `codex.exe --version`
2. `codex.exe login status`, only after a valid Codex version
3. `agy.exe --version`

Only fixed PATH-discovered native executables are considered; shell wrappers and
request-selected executable paths are denied. Slot failures do not trigger retries
or additional commands. CLI output is reduced to existing closed enums and bounded
version strings. There is no provider/model generation, API call, secret-file scan,
browser action, Task Scheduler change, listener install or account login mutation.
A future installed operator must contain subprocess trees and reconcile native
completion; this source adapter does not install a Windows process supervisor.

Public Issue ingress has no private-health operation. Both `parse_event()` and
`perform()` deny it; the existing `PERSONAL_PUBLIC_FIELDS`, v3 public sanitizer and
all sixteen standing READ categories remain unchanged. Never post private reports
or raw request/response payloads to public Issues, including by embedding them in
an otherwise permitted string. Source tests cover public routing denial and the
existing protected sanitizer's private-field rejection.

## Requirements before any later trusted private bridge

- Install and authenticate a connector/operator on the intended Windows host;
  establish tool permission and explicit private-read authorization.
- Pin authenticated origin and exact current chat, host identity, installed
  release/source, and private recipient. Verify detached authorization over all
  canonical request bytes; do not trust request-supplied flags.
- Generate random nonces, enforce UTC TTL/freshness and atomic durable monotonic
  replay protection, and bind returned signed native readback to that request,
  host, source and origin/chat. Handle restart, revocation, concurrency and crash
  recovery without silently replaying an uncertain native effect.
- Verify private account identity independently with authorized tools. Integrate
  documented provider quota APIs under separate permission and a reviewed schema
  revision before reporting any actual quota. Do not infer capacity from login.
- Keep payloads on a private authenticated channel with bounded disclosure. A
  source merge or independent test PASS supplies none of these installed effects.

## Verification handoff for the new #597 successor

Historical #580/#597 failed attempts and quarantined patches remain immutable.
This implementation is a new source attempt from main
`426ef49748ca27ea8d4216743c58b9b48049e6d2`; it does not repair old input manifests
or change the already merged bounded AGY cleanup rootfix.

The persisted `hostops-personal` owner remains unchanged: source `personal.py`,
owner tests `test_hostops_personal.py`, consumer `hostops_v3/contract.py`. The
registered consumer group is `hostops-v3`. Added tests cover strict wire types,
authorization, origin/chat, TTL/future time, nonce/sequence/time replay, capacity,
busy admission, completion freshness, private-data suppression, fixed probes,
unknown quota/policy, public denial and present owner/consumer registration.

The Codex provider must not execute tests, Python imports or background helpers.
It edits only the three authorized files. All pytest caches, bytecode, JUnit,
build products and other generated files must stay out of the immutable input and
source worktree. Nothing may alter or delete the input mirror to conceal drift.
After the provider returns, EF01 owns real Windows preflight and registered
`mt01-component-onboarding` selection, with actual xdist12 owner and downstream
consumer selected/collected/executed evidence, separate disposable scratch,
input integrity, exact path guard, source hashes after verification/teardown,
clean scratch teardown and Job Object `tree_empty` reconciliation. Only EF02 may
publish the verified candidate, followed by hosted Windows PR CI, independent
Brain PASS and trusted merge. Provider prose and the presence of tests are not
evidence of execution; absent those host receipts the result remains **UNPROVEN**.
