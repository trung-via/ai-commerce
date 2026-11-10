# BQ-05b.1 — Private broker no-secret trial handoff (CI-first source-only)

## Boundary and history

Human selected a **fresh independent source reauthoring** in [Issue #767](https://github.com/trung-via/ai-commerce/issues/767) on `main@0e6e420dc10e390d2c9d3db3b0c98adfebe40cb5`. This is **not** a reexecution, retrieval or transfer of the original #646 Codex EF02 attempt. That old WorkOwner journal remains `UNKNOWN`, tip `2dd88fb49d948eab1f37e4691d55a392bf112181`; #656 remains its native reconciliation owner. The #656 source-only reconciliation itself is already merged via PR #761. No historical journal, provider call or private receipt is changed here.

## Offline interface

`prepare_broker_trial_handoff(session_path, selection=None, at=None)` replays BQ-05b.0 `inspect_effect_preflight` then `recheck_preflight` using **new native BQ-05a Q4/Q5 media checks**. The upstream owner validates actual B0.4 `WAIT_BRAIN` journey, A06 current media SHA, durable packet/receipt/journal checkpoint and provenance. BQ-05b.1 exposes `BQ05bBrokerTrialHandoff.v1` canonical in-memory bytes with three states deliberately kept distinct:

- `LOCAL_TRIAL_INPUT_BOUND`: current bytes and Q4/T1/T2 observability exist. This is technical evidence **only**.
- `FAIL_CLOSED_HOLD`: missing/stale native media, wrong stage, invalid untrusted selection or unsupported action/context. No partial native binding escapes.
- **External effects remain NOT_AUTHORIZED** in both cases; there is no private issuer enrollment, effect grant, spend, upload or publication.

`recheck_broker_trial_handoff(raw, session_path, ...)` reparses strictly closed canonical bytes and recomputes all underlying native owners. Hashes identify content, not independent signatures, valid rights, account ownership, provider payment/receipt or human approval. A self-resealed altered report is rejected by current owner replay.

## PRIVATE_BROKER_TRIAL_INTERFACE_V1

Only fixed field **names**, no effect values: current journey/directive/video/preflight byte identity is reduced to a deterministic request digest. The `challenge_label` is an explicitly unauthenticated design label, **not** a nonce, challenge proof or grant. Account, vendor, model, region, spend, currency and target are not selected or serialized. An optional `BrokerSelectionDraft.v1` is parsed by existing B0-S3b owners, and remains `UNTRUSTED_DESIGN_SELECTION`, never `PRIVATE_VERIFIED`.

Six deterministic adversarial scenario labels cover native-byte/WYSIWYS, secret custody and issuer independence, wrong identity/target/rights, spend cap/currency/revocation, idempotency/UNKNOWN-effect fencing and actual provider/Human receipts. Every vector has expected `DENY_UNTIL_AUTHENTICATED`, evidence status `NOT_OBSERVED`. They are conformance requirements for a **future independently authenticated private broker**, not executable live requests, signed artifacts or test-generated credentials.

The source must never elevate `LOCAL_TRIAL_INPUT_BOUND` into `LIVE_EFFECT_ALLOWED`, `VERIFIED_BROKER`, an authenticated BrainDecision, a provider approval, atomic budget reservation or replay authorization.

## Genuine verification gates

Tests in `tests/test_bq_05b1_broker_handoff.py` import actual existing B0.4 Windows CLI + FFmpeg A06 `journey` fixture; check current native bytes, Q4 T1/T2 observations, native journal/packet/receipt and no mutation, then negative stale media, journal/receipt edits, self-sealed forgery, wrong/expired broker drafts, untrusted context, live action, pre-WAIT_BRAIN and hardlink. Native filesystem capability failures remain **UNPROVEN**; no test should convert them into synthetic PASS.

MT-01 `bq-05b1` registers three owner ACs, exact test and downstream source consumers covering BQ-05a/BQ-05b0, B0-S3b, B0.3/B0.4, A06 Q4/Q5 and three closed PULSE v1 contracts. Run minimal owner+consumer first; require authentic selected/collected/executed JUnit coverage, exact HEAD/base hosted Windows CI and independent Brain semantic review before trusted merge. No CI result, independent review, PR merge, video generation, right or broker effect is asserted by this document.

## Operating prohibition

Do not call providers, access a credential, enroll issuer, issue/consume idempotency key, reserve budget, retry UNKNOWN, transfer WorkOwner, alter protected EF01/EF02/MT01 workers, touch private Windows/HostOps/Chrome 9444 or PULSE live process. Any actual external-effect test needs separate Human and independently verified private authority.
