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

1. **Candidate implemented, inactive:** private transactional `TurnSessionStore.create/read/record_intent/observe_reply` with bounded JSON, native Windows SID+DACL/POSIX owner checks, OS lock, and atomic writes. The legacy/manual `observe_reply` path proves **Human UI observation only**, not GitHub authenticity. A separate `observe_verified_reply` method now requires BOTH Human UI confirmation AND `GitHubTurnEvidenceAuthority.verify` to independently re-fetch a GitHub comment with an operator-pinned author account ID and exact typed JSON packet (`handoff_id`, issue, role, turn index, source SHA), and confirms current `main` SHA before CAS-guarded state transition. A GitHub comment alone still does not prove ChatGPT response/plugin invocation or authorize any Send.
2. **Candidate implemented, disabled:** `plugin_turn_stage_only.ExplicitTurnChipStager(enabled=False)` accepts only a separately validated `ResolvedBinding`, a `READY` ledger session, and literal one-shot `operator_approved=True` on Windows. It finds exactly one matching existing tab, rejects preexisting draft, types **only one** known role alias (`@AI Commerce Brain` / `@AI Commerce Engineering Workflow`) plus Space after full-name check, then reads the existing DOM chip probe. It does **not** append a prompt, navigate, click Send or clear drafts; every ambiguous input stops for Human inspection/cleanup. All UI results keep `host_selection_proven=False`. A Future caller must first independently read the durable session and authenticated route; this module alone is NOT route enrolment, persistent approval or automatic Turn/Wake execution.
3. **Only with an additional explicit Human authorization** for new test deliveries, add write-ahead attempt persistence, a single exact-chat Send, and post-Send reconciliation with no blind retry on `UNKNOWN`.
4. Verify each ChatGPT role turn independently from the host's actual observable tool trace, not from a role-play response. If external ChatGPT does not expose plugin invocation identity, report `DIRECT_PLUGIN_INVOCATION_UNPROVEN` even if an @ chip is blue. A multi-turn router is not the same as native same-turn plugin-to-plugin invocation.
5. Return Engineering evidence via a bounded GitHub ref, then Brain performs a separate final turn. No EF02 dispatch or merge without independent authorization.

## Future Send write-ahead gate — code-only, disabled from CLI

The candidate now includes `TurnSessionStore.record_intent_checked(expected_state=..., operator_approved=True)` for a **future, separately authorized** Send caller. Unlike legacy `record_intent`, it validates an exact prior `READY` snapshot and checks equality **inside the transaction lock**, then atomically and durably writes `SUBMISSION_UNKNOWN` before any external side effect. It rejects missing approval, stale snapshots, an already pending/complete turn, and concurrent attempts without advancing state or retrying. Tests cover approval refusal, stale SHA/snapshot, write-before-Send UNKNOWN persistence and concurrent single-winner CAS. This API never opens Chrome or calls Send; it does not authenticate the GitHub main SHA or exact-chat route itself.

For an actual Send, the trusted caller must **separately** check live `main` SHA versus ledger source, exact private route, correct chosen plugin, empty composer and one-shot Human authorization, then perform `record_intent_checked` immediately before one authorized Send. If Send fails or receipt is ambiguous, leave `SUBMISSION_UNKNOWN` and require independent Human UI + authenticated GitHub evidence to reconcile; do not auto-retry. **Neither this module nor the existing CLI contains an active Send pathway; operator private ledger is unchanged by code publication.**

## Non-goals and controls

No hook into `worker.py` or existing D2/D3/D4 Send route, no GitHub Action dispatch, no active watchdog, no browser launch, no automatic new chats, no automatic Send, no fake ChatGPT ACK, no retry of #161/#177/#179, no change to PR #186. This pure module is safe to keep unactivated while acceptance expands.


## Inactive operator CLI (v1 candidate)

`python -m ai_commerce.remote.plugin_turn_cli` is deliberately **not** a background dispatcher. It accepts only a pre-provisioned operator-private ledger file outside the repository and, for type-only mode, the previously consented D5 exact-route registry:

- `--mode preview` (default) reads the private ledger and returns role and turn index only; it cannot attach Chrome.
- `--mode create-ledger --issue-number 181 --source-sha <40-hex>` requires interactive exact token `CREATE-D5-PRIVATE-LEDGER`; creates an unactivated private session once, never overwrites an existing one. Source SHA must be verified by operator/current GitHub separately.
- `--mode type-only --registry <private-registry> --route-id <enrolled-route>` requires an existing `READY` ledger session, private registry consent, exactly one matching existing chat tab and a fresh, turn-specific interactive token `TYPE-ONLY-D5-<ROLE>-<INDEX>`. It types one @ plugin chip and observes; **no task text, ChatGPT Send, automatic draft cleanup or retry**. It does not advance the ledger, because staging alone is neither a message nor a plugin invocation. After the interactive consent token, CLI re-reads **both** the private ledger snapshot and the pre-enrolled exact-chat registry binding/consent; either change fails closed **before typing**. These are bounded preflight checks, not a cross-process CDP lease or proof that ChatGPT accepted a plugin.

The operator must reconcile each unsent draft manually. A `SUBMISSION_UNKNOWN` state is never eligible for staging another turn. No stored registry/chat URL, CDP credentials, or host/plugin receipt are published in GitHub. These CLI commands are **not deployed or live-tested on the operator's Windows host**. Running them, if later separately authorized, requires syncing the candidate code and matching the current D5 private files; no guessed private paths.

## Authenticated GitHub evidence candidate (inactive)

`plugin_turn_evidence.GitHubTurnEvidenceAuthority` uses an operator-provided GitHub token provider and the existing D3 bounded GitHub REST JSON reader. It constructs only two fixed GET URLs itself: `GET /repos/trung-via/ai-commerce/issues/comments/<comment-id>` and `GET /repos/trung-via/ai-commerce/branches/main`. It never treats a user-provided URL as a fetch target.

The comment **must** be a plain, complete JSON object with schema `ai-commerce-d5-turn-observation-v1`, exactly these fields: `schema`, `repository`, `issue_number`, `handoff_id`, `turn_index`, `role`, `source_sha`, `observation="OPERATOR_UI_RESPONSE_OBSERVED"`, `host_selection_proven=false`. A separately pinned numeric GitHub author ID must match the API's comment author; the Issue reference, comment ID, unedited creation timestamp and live main SHA are checked. Wrong author, duplicate keys, stale main SHA, wrong role/step or partial body all fail closed. This is **not a cryptographic signature nor an authenticated ChatGPT tool event**. Each step additionally requires Human-confirmed UI response and an unchanged private ledger; a separate Send permission remains necessary.

The existing `observe_reply` method remains available for explicitly labeled **manual UI-only** sessions and MUST NOT be silently substituted for `observe_verified_reply` by a future unattended router. No token is loaded or GitHub called by default CLI preview/type-only. There is no GitHub write from this authority and no network at import time. Latest tests are mock-read only; the real token-backed path is **not operator-Windows live verified**.
