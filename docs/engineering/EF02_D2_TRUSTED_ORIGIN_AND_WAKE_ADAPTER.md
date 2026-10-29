# EF02-D / D2 — Trusted Origin & Local Wake Adapter

**Scope:** independent AI Commerce repo. **Status:** local component candidate; no live ChatGPT account activity or fake delivery receipts.

## Boundary and Architecture

- D1 implemented passive completion event projection and read-only route resolution. D2 establishes the **local trusted-origin authority**, **evidence-bearing provenance verifier**, **durable idempotency store**, and **local-only isolated wake adapter**.
- **Independently Trusted Origin Authority:** A public completion event digest is not authentication. D2 rejects any route claim supplied by GitHub bodies, Issue comments, or model memory. All routes and claims are managed by a local origin registrar (`LocalOriginRegistrar`) stored privately outside the repository.
- **Explicit Human Consent & Exact Target:** Route registration demands explicit human opt-in (`consent=True`), positive integer generation, valid loopback CDP endpoint, and an exact ChatGPT conversation URL containing its unique UUID. Implicit capture of "current tab" or "latest chat" is strictly prohibited.
- **Provenance Verification:** Before attempting any wake, `verify_provenance()` validates the completion event against the final `batch_result.json` (`wake_event_status=RECORDED_UNBOUND`, matching `event_id`, repository, source SHA, run ID, run attempt, issue number, and actions) **and** a separately verified GitHub artifact provenance context (`GitHubArtifactProvenance`). Unverified artifact provenance immediately fails closed with `PROVENANCE_UNVERIFIED` and no send.
- **Local Secret Isolation:** Chat URLs, CDP endpoints, caller tokens, and cookies remain strictly local. Dataclasses (`ResolvedBinding`, `LocalCallerAttestation`, `GitHubArtifactProvenance`, `WakeReceipt`) enforce `repr=False` with scrubbed string representations to prevent accidental leakage into GitHub logs, issues, or artifacts.

## Delivery States & Durable Idempotency

The idempotency store (`DurableIdempotencyStore`) is keyed by `(event_id, route_id, generation)` outside the repo. Delivery transitions through four explicit states:

1. `NOT_ATTEMPTED`: Initial state before delivery is initiated.
2. `UNKNOWN`: Write-ahead state recorded prior to dispatching transport requests. If a crash or timeout occurs mid-send, subsequent runs detect `UNKNOWN` and **hold uncertain sends** (`DELIVERY_UNCERTAIN_HELD`), refusing blind retry or duplicate messages.
3. `UNSUPPORTED`: Platform channel or precondition cannot be proven (e.g. unverified origin, exact chat tab not open, unauthenticated session, captcha, non-loopback CDP, missing provenance).
4. `CONFIRMED`: Marked **only** when delivery is independently witnessed within the exact chat DOM (e.g. verified turn receipt in page transcript). Click success, HTTP 200, CLI exit 0, or GitHub Issue comments are explicitly **not** delivery proof.

Native Brain continuation is kept strictly independent and always marked `brain_continuation_proven=False` (`NOT_PROVEN`).

## Wake Adapter Interface & Strict Browser Isolation

- `LocalCdpWakeAdapter` inspects open page targets on the loopback CDP endpoint.
- **Exact Tab Matching:** Exactly one target matching the registered chat URL must be open. If zero tabs match, delivery fails closed with `EXACT_CHAT_TAB_NOT_FOUND`. If more than one tab matches, delivery fails closed with `AMBIGUOUS_CHAT_TARGETS`.
- **No Navigation / No New Chat:** The adapter never opens a new tab, never navigates an existing tab to a different URL, and never selects a default or active tab.
- **Authentication & Captcha Guard:** Unauthenticated sessions or login/challenge barriers fail closed with `SESSION_UNAUTHENTICATED_OR_CAPTCHA`. No captcha bypass or undocumented private API is attempted.

## Operational Prerequisites

1. **Local Chrome / Chromium Profile:** Running with `--remote-debugging-port=9222` bound strictly to loopback (`127.0.0.1` or `localhost`).
2. **Pre-opened Target Conversation:** Human operator has authenticated and navigated to the exact conversation URL before registration.
3. **Local Caller Attestation:** The calling process must present a verified local caller attestation (`is_local=True`, `attestation_verified=True`).
4. **Verified GitHub Artifact Provenance:** Workflow run artifacts must be verified and attested before supplying to the local bridge.
5. **Outside-Repo State:** Both the route registry (`chat_routes.json`) and idempotency store (`idempotency.json`) must reside in an absolute path outside the checkout root.

## Honest Limitations

- ChatGPT provides no supported external conversation-write API. Delivery relies on an operator-consented browser profile session via loopback CDP.
- If Cloudflare, captcha, or session expiration occurs, delivery cannot proceed and safely reports `UNSUPPORTED`.
- Replaying a confirmed event returns the cached receipt with `replayed=True` and produces zero browser side-effects.

## Remaining D3 Live Validation

- Real multi-chat concurrent routing (A/B chat isolation) against live browser sessions.
- In-flight network disruption recovery and DOM witness timeout verification under live conditions.
- Operational integration with offline artifact staging.
