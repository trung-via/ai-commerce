# EF02-D4 — AIOS-style Local Regular Chat Doorbell via Chrome CDP

Date: 2026-10-30. Tracks [Issue #109](https://github.com/trung-via/ai-commerce/issues/109). Human clarified that earlier AIOS-Renew ChatGPT Work wake was not viable and switched to local Chrome CDP doorbell. This branch implements the **transport direction they actually chose**, without importing AIOS runtime, Work watchers, cookies, credentials, tokens, Chrome profiles, TASK lifecycle or planning authority.

## Source-grounded adaptation

AIOS-Renew source `trung-via/AIOS-renew@cbefb926`: `src/aios_renew/local_chat_wake.py` and `docs/AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md`. Its `BrowserAdapter` is attach-only, chooses exactly one preexisting bound page, rejects draft/busy/login/wrong chat, stages input then checks the app-managed Send state, performs a single click and observes the exact user turn without reading the assistant's response. H4D additionally has unattended profile acquisition, which is **not copied** in D4.

AI Commerce `LocalChromeDoorbellAdapter` is a new independent, **explicitly opt-in** `WakeAdapter`. The default D2 `LocalCdpWakeAdapter` remains probe-only, and the new adapter does not replace it or run automatically from an Issue.

## Browser boundary

To use this in a future authorized interactive Windows composition root:

1. Install the separate optional Python extra `pip install -e ".[local-chat-wake]"` in an environment with an interactive, already-authenticated, explicitly consented Chrome CDP endpoint. This branch **does not install Playwright or start Chrome on the operator's laptop**.
2. Use the existing D3.1 `GitHubRestArtifactAuthority` and D3.2 `SignedOriginConsentAuthority`, with real operator-provisioned credentials, independently observed principal, registered exact conversation, exact batch claim and BOTH `resolve_claim_route` and `deliver_wake` grants. Untrusted caller flags are insufficient.
3. Only after operator authorization, inject `LocalChromeDoorbellAdapter(allow_submit=True)` into D2 `deliver_exact_chat_wake(..., adapter=...)`. **Do not** set this default for all EF02 jobs, do not activate in a headless Windows service, and do not replay existing UNKNOWN events. A fresh, authenticated event/route/generation is required.
4. D2 writes a durable `UNKNOWN` intent before invoking the adapter. The adapter attaches by `playwright.chromium.connect_over_cdp` to the exact stored local port. It **does not** launch Chrome, create/navigate tabs, choose the active/newest page or copy any profile. It requires exactly one page with the exact registered ChatGPT URL (including Project-scoped paths), safe regular-chat UI, empty composer and no existing exact outbound turn.
5. The adapter constructs a bounded doorbell from `event_id` and the pinned repository, ignoring caller-provided batch summary (which is not semantic authority): `[AI COMMERCE LOCAL CHAT WAKE]`, event_id, repository, and `fresh_brain_sync_required: true`. It inserts native editor input, waits for the app-controlled Send button to become available, rechecks exact page and sends **at most one click**. No assistant output is read.
6. If a visible exact outbound user turn is observed, D4 returns `delivery_status=UNKNOWN`, `error_code=UI_USER_TURN_OBSERVED`, with `chat_delivery_proven=false` and `brain_continuation_proven=false`. This is **browser-side transport evidence**, not server-authenticated delivery. On error or ambiguous click it stays UNKNOWN and is held; no blind retries. D2 may report `UNSUPPORTED` for missing capability or unsafe pre-send surface.

## Why not Work/MCP Events?

The Human already tried Work chat in AIOS-Renew and moved away from it. Separate GitHub-triggered Work tasks cannot prove writing into an existing regular chat and previously consumed shared Work quota. MCP Events are not being implemented as a replacement wake transport. No use of undocumented ChatGPT HTTP endpoints, cookies, CAPTCHA bypass, anti-bot/stealth evasion or assistant response scraping.

## Validation status / remaining acceptance

- Deterministic unit tests simulate exact page selection, busy/draft, refusal of duplicate outbound, explicit opt-in, one click, user-turn observation and ambiguous crash.
- GitHub CI verifies the package and regression tests on Windows and Ubuntu (pending on PR creation).
- **NOT LIVE VERIFIED:** No real ChatGPT message, browser CDP session or real operator consent has been exercised by CI. Playwright selectors/editor behavior must be checked in the user's authenticated interactive Chrome with their explicit action. Windows self-hosted runner may be non-interactive or disconnected from the Human's Chrome session.
- **NOT COMPLETE:** Standalone GitHub-to-local source event trigger, source-affine unattended Chrome acquisition and independent exact-chat server receipt/Brain continuation conformance. These remain separate follow-up work; no false PASS.
- Where the chat UI is not supported, unavailable, logged out or showing CAPTCHA, fail closed, leave GitHub feedback/artifact as the reliable fallback and request Human intervention.

Rollback: revert the new `cdp_doorbell.py`, package export, optional extra, allowlisted diagnostics, tests and this document. D1–D3 remain unaffected.
