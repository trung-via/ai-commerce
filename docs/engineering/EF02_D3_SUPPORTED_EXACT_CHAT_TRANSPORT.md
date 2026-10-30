# EF02-D3.3 — Exact-chat transport capability decision (2026-10-30)

Tracking issue: [#109](https://github.com/trung-via/ai-commerce/issues/109).

This document is an engineering capability gate and bounded integration test of D3.2. **No real ChatGPT Wake or native Brain continuation has been proven.** The D2 `deliver_exact_chat_wake` function still disallows all self-asserted `CONFIRMED` delivery receipts, so a fake adapter cannot turn a mock post into success.

## Supported host facts, not assumed API capabilities

1. [Plugin Extensions reference](https://developers.openai.com/plugins/build/extensions) documents **Extended message sending** to the *current conversation* or a new one on supported desktop and Work web contexts. It does **not** establish a Windows background process API to post into arbitrary previous ChatGPT conversation IDs, nor does it establish an independently authenticated receipt.
2. [ChatGPT Work scheduled and event-triggered tasks](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt) documents supported **GitHub pull request activity** triggers for eligible users; the resulting Work task is distinct from the original requesting ChatGPT conversation.
3. [Connected apps](https://help.openai.com/en/articles/11487775-connected-apps-in-chatgpt) mentions *where supported* subscription updates into a conversation. Availability, exact supported GitHub event types, conversation binding and receipt verification must be established in the actual host before using this as a Wake path.

**Classification:**

| Path | Can react to GitHub activity? | Guaranteed to append to preexisting exact chat? | Delivery receipt proven? |
|---|---|---|---|
| GitHub Issue + run artifact | Yes (D1 fallback) | No | Not applicable |
| ChatGPT Work GitHub-triggered task | Supported for eligible GitHub PR events | No documented guarantee | Work task event only |
| Plugin Extension message send | In supported current/new conversation contexts | Not documented for arbitrary prior chat | Not proven for this EF02 bridge |
| Connected-app subscriptions | In some supported hosts | Must be checked in user's environment | Not proven for this EF02 bridge |
| D2 CDP probe | Probe-only and fail-closed | No write | No |

## Exact route and consent checks before transport

Even when a supported host transport becomes available, its trusted composition must:
- Supply D3.1 `GitHubRestArtifactAuthority` with an operator-controlled read-only token and pinned authenticated GitHub run bytes.
- Supply D3.2 `SignedOriginConsentAuthority` with a private operator-issued HMAC key, independently obtained local principal and **two distinct signed grants**, `resolve_claim_route` and `deliver_wake`, both pinned to the current validated registry hash and event identity.
- Confirm explicit user consent, exact chat, route generation, authenticated host context and supported write capability **before** a send attempt.
- Persist intent before any send, never retry an `UNKNOWN` event automatically, and treat self-reported adapter status/click/DOM match as insufficient to claim delivery.
- Only promote status to `CONFIRMED` after a reviewed host-supported independent receipt is available, tied to event ID, route, generation, exact thread and message identity. The existing D2 code has **no such verifier** and MUST stay fail-closed until one is implemented.

## Verification and limitations

`tests/test_ef02_d3_two_grant_delivery.py` exercises the complete D2 pipeline from signed registration and claim through grant resolution, fail-closed adapter invocation and deduplication. The test uses **test-only** artifact authority and adapter: it does not prove authenticated live GitHub token access or ChatGPT activity. Missing either of the required origin grants prevents adapter invocation; both valid grants permit only an `UNSUPPORTED` probe exactly once.

**Next:** Authorize/configure a supported ChatGPT host route or connected-app subscription in the actual user workspace, verify if it attaches to the original conversation (not a new Work chat), and obtain an authoritative receipt contract. If no such capability is available, use event-triggered Work as *separate autonomous follow-up*, retaining GitHub Issue/artifact as durable evidence.

No changes to EF02 execution, Git Write Guard, GitHub secret permissions, CDP message posting or automatic merge are authorized by this document.
