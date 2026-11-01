# D5 explicit per-plugin turn handoff v1 — PREVIEW ONLY

Status: **candidate design, not active**. Context: Issue #181, 2026-11-01.

## Evidence and decision

- Human-confirmed Chrome CDP chip staging: **Brain-only**, **Engineering-only**, and **dual** all show the correct blue chips (3/3 UI PASS). This **does not** prove native host plugin invocation.
- Single Brain-only Send: `UNSUPPORTED_OR_UNPROVEN` for Brain invoking an independent Engineering plugin.
- Single Engineering-only Send: `DIRECT_PLUGIN_INVOCATION_UNPROVEN`. The skills and shared GitHub calls are real; the current plugin manifest `interface.capabilities: []` declares no independent Engineering MCP action.
- Human approved explicit routing by separate turns. **No approval was given to automatically activate Send, dispatch EF02, or replay an existing D2 UNKNOWN event.**

## Target sequence

`Brain scope/decision (turn 0) -> Engineering read-only review (turn 1) -> Brain reconcile/decide (turn 2)`.

Each turn is an **independent** ChatGPT submission in a previously operator-enrolled, exactly bound chat. A new chat requires an independently enrolled route; neither a GitHub packet nor latest/active tab is allowed to select a private conversation. The GitHub issue is only a public coordination/evidence reference; never store browser cookies, profile, CDP endpoint, private route ID, or chat URL in GitHub.

D5 v1 `plugin_turn_handoff.py` remains a **pure non-delivering state machine**. `plugin_turn_store.py` adds operator-private, owner/ACL-checked, atomically persisted transitions using the existing `PrivateJsonState` lock and never accesses the browser. The operator must provision the private parent directory outside the repository; no production state is created or migrated automatically. The ledger enforces:

| Current | Event / proof | Next | Limit |
|---|---|---|---|
| `READY` | Explicit operator approval, caller *durably* records write-ahead intent | `SUBMISSION_UNKNOWN` | Browser Send remains external and disabled here |
| `SUBMISSION_UNKNOWN` | Operator-verified UI reply, GitHub issue-comment evidence pointer | `READY` for next role | Observation is NOT host invocation/server ACK |
| Final `SUBMISSION_UNKNOWN` | Operator-verified final UI reply | `COMPLETE_UI_ONLY` | No claim of autonomous handoff |
| `SUBMISSION_UNKNOWN` | Timeout/error/ambiguous submission | Remain `SUBMISSION_UNKNOWN` | Never automatically retry, reselect or replay |

`handoff_id` identifies a new episode, distinct from EF02 IDs and previous D2 events; caller generates a unique ID. `source_sha` pins a planning reference; the executing reader must separately fetch live source and reconcile drift. `intent_fingerprint` is deterministic idempotency bookkeeping, **not** a signature, proof of origin, a GitHub write lock, or authorization to send. `operator_verified=True` is a Human report, not a cryptographic attestation. GitHub evidence references are locators, not authenticated receipts.

## Integration order and missing acceptance

1. **Candidate implemented, inactive:** private transactional `TurnSessionStore.create/read/record_intent/observe_reply` with bounded JSON, native Windows SID+DACL/POSIX owner checks, OS lock, and atomic writes. This store does not authenticate GitHub evidence; the future caller must independently fetch the comment and validate issue/step/result and source SHA before requesting reconciliation. A GitHub comment URL by itself does not authenticate content.
2. **Candidate implemented, disabled:** `plugin_turn_stage_only.ExplicitTurnChipStager(enabled=False)` accepts only a separately validated `ResolvedBinding`, a `READY` ledger session, and literal one-shot `operator_approved=True` on Windows. It finds exactly one matching existing tab, rejects preexisting draft, types **only one** known role alias (`@AI Commerce Brain` / `@AI Commerce Engineering Workflow`) plus Space after full-name check, then reads the existing DOM chip probe. It does **not** append a prompt, navigate, click Send or clear drafts; every ambiguous input stops for Human inspection/cleanup. All UI results keep `host_selection_proven=False`. A Future caller must first independently read the durable session and authenticated route; this module alone is NOT route enrolment, persistent approval or automatic Turn/Wake execution.
3. **Only with an additional explicit Human authorization** for new test deliveries, add write-ahead attempt persistence, a single exact-chat Send, and post-Send reconciliation with no blind retry on `UNKNOWN`.
4. Verify each ChatGPT role turn independently from the host's actual observable tool trace, not from a role-play response. If external ChatGPT does not expose plugin invocation identity, report `DIRECT_PLUGIN_INVOCATION_UNPROVEN` even if an @ chip is blue. A multi-turn router is not the same as native same-turn plugin-to-plugin invocation.
5. Return Engineering evidence via a bounded GitHub ref, then Brain performs a separate final turn. No EF02 dispatch or merge without independent authorization.

## Non-goals and controls

No hook into `worker.py` or existing D2/D3/D4 Send route, no GitHub Action dispatch, no active watchdog, no browser launch, no automatic new chats, no automatic Send, no fake ChatGPT ACK, no retry of #161/#177/#179, no change to PR #186. This pure module is safe to keep unactivated while acceptance expands.
