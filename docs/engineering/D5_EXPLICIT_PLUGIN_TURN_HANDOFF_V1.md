# D5 explicit per-plugin turn handoff v1 — operator-controlled implementation

## Automatic exact-chat URL loading (candidate, no live deployment)

The enrolled private route registry remains the only source of the exact chat
URL. On Windows with Chrome CDP **9333 already listening**, the new pre-Wake
preflight finds exactly one matching enrolled chat, or opens a new Chrome tab to
the bound URL if none exists. No active/latest tab selection or unrelated-tab
navigation; multiple matching tabs or ambiguous Chrome contexts fail closed.
No private URLs, cookies or messages are published to GitHub.

Local D2/D4 Wake preloading occurs only **after** existing signed origin,
artifact provenance, consent and write-ahead UNKNOWN. It never replays old
events, auto-retries an UNKNOWN, opens Chrome without a listener, or claims
ChatGPT server ACK. New D5 operator CLI `--mode open-chat` loads only the chat.
Existing `prepare-turn` now auto-loads before staging the @ chip and exact
prompt; neither mode presses Send. Send remains separately Human-authorized.

The operator-facing status sequence is `EXACT_CHAT_PRESENT` or
`EXACT_CHAT_OPENED` → `TURN_PREPARED_NO_SEND` →
`SUBMISSION_UNKNOWN` → `UI_REPLY_RECONCILED_ONLY`.
A graphical dashboard may represent these stages later; this change improves
navigation and explicit status codes, not a new browser-based dashboard.
This candidate needs exact-head CI, review and separate live Windows activation.



Status: **ported implementation candidate for operator-controlled CDP 9333 operation; no live activation from GitHub CI**. Context: Issue #181, 2026-11-02.

## Operator use — single existing enrolled chat, no Work or MCP (2026-11-02)

This is the production-candidate **Windows local CDP** route, not a ChatGPT Work task. It reuses the three previously observed UI role turns (Brain, Engineering, Brain). It deliberately does **not** run unattended browser sends or silently switch plugin roles; no new chip UI acceptance experiment is requested.

Prerequisites: checkout from an approved, reviewed commit; an authenticated Chrome 9333 session with a separately enrolled exact-chat route registry; a private directory **outside** the Git repository provisioned with the existing D5 owner/SID/DACL rules; GitHub CLI `gh` authenticated for read access. Never copy any private registry, chat URL, browser cookies, user message, or bearer token into a GitHub Issue/PR.

The commands below are templates. Substitute only operator-owned pre-enrolled paths and a freshly verified main SHA. **Do not reuse events #161, #177 or #179** or mutate the historical private ledger.

```powershell
$common = @("--checkout", "<approved-checkout>", "--state-file", "<private-turn-ledger>")
$route = @("--registry", "<pre-enrolled-route-registry>", "--route-id", "<existing-exact-route-id>")

# One NEW, authorized session (requires Human CREATE-D5-PRIVATE-LEDGER token)
python -m ai_commerce.remote.plugin_turn_cli @common --mode create-ledger --issue-number 181 --source-sha <fresh-40hex-main>

# Repeat ONLY for a READY turn: Brain index 0, Engineering index 1, Brain index 2
python -m ai_commerce.remote.plugin_turn_cli @common --mode preview
python -m ai_commerce.remote.plugin_turn_cli @common @route --mode prepare-turn
# Check staged @ chip and complete exact text, then explicitly consent one Send
python -m ai_commerce.remote.plugin_turn_cli @common @route --mode send-once --enable-send-once
# STOP: SUBMISSION_UNKNOWN until actual UI reply is independently observed.
# Publish an exact-form, trusted-author observation packet to the bound GitHub Issue
# and confirm the UI reply yourself; never infer independent plugin call from chip alone.
python -m ai_commerce.remote.plugin_turn_cli @common --mode observe-reply --comment-ref <exact-issuecomment-url> --expected-author-id <pinned-author-numeric-id>
```

- `prepare-turn` selects the expected **one** @ plugin chip using CDP and types exactly the next prepared role prompt. It **never sends**. Any extra draft, ambiguous chip, route change, active generation, or failed exact-draft guard stops for manual cleanup.
- `send-once` rechecks exact chat, current main SHA, private state and complete draft, then atomically records `SUBMISSION_UNKNOWN` **before** clicking Send once. Its result remains UNKNOWN even if a button click was observed. It returns nonzero intentionally; do not retry or loop it.
- `observe-reply` is an independent Human UI + trusted GitHub evidence reconciliation only. It requires a current matching SHA, a Human token, an authentic JSON comment authored by the pinned GitHub account and an unchanged private ledger. It then advances to the next role or `COMPLETE_UI_ONLY`; this does not establish a ChatGPT host-native plugin receipt. `expected_observation_packet(session)` provides the exact schema for the authorized public observation comment; do not publish private chat contents.
- Human authorization for new EF02 tasks, model spending, merges, writes or provider calls is **separate**. The text of a GitHub comment is never authorization to execute.
- The existing EF02 completion artifact is a grounded input to Brain, **not** an automatic browser Send command. This patch neither sends a historical Wake nor adds a new implicit GitHub Actions watcher.

**Activation condition:** This PR must first pass exact-head CI/review and receive separate Human approval to merge/deploy. Existing saved D5 UI-only 3/3 results remain accepted without rerunning Chrome tests; the code change requires ordinary unit/contract CI only.

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


## One-Shot Send candidate — disabled and operator-gated (2026-11-01)

**Status: published only in Draft PR #187; not activated; no real Send accepted.** The \`plugin_turn_send_once.ExplicitTurnOneShotSender\` gate is OFF by default, Windows interactive only. The new CLI \`--mode send-once\` exits with \`SEND_DISABLED\` unless \`--enable-send-once\` is explicitly provided, independently of the existing preview/type-only modes. Even with that flag, the caller must supply a secure out-of-repo ledger and an **existing consented, exact** private route registry, and separately enter \`SEND-ONCE-D5-<ROLE>-<INDEX>-<HANDOFF_ID_SUFFIX>\` for this individual turn. This is **NOT** permission inferred from a prior \`CREATE-D5-PRIVATE-LEDGER\` or \`TYPE-ONLY\` token, and this document is not authorization to run Send.

**Precondition:** A Human must have manually staged precisely one structured chip for the preview's role and the **exact full** \`preview_next_turn(session)["prompt"]\` text in the same composer, with no extra words or other plugin chip. One exact open ChatGPT tab must match the enrolled route. The sender does not auto-stage, navigate, select latest/active tab or clear composer. Both the consented route and a fresh \`gh api repos/trung-via/ai-commerce/branches/main --jq .commit.sha\` result must match the original ledger before and after the one-shot Human prompt. The browser guard validates exact URL, single visible composer/form, idle generation, no login/Work surface, one expected structured plugin candidate, absence of the other plugin, exact staged text and one enabled Send button. Strict DOM mismatch fails closed without mutating the composer or ledger.

**Irreversible boundary:** After all read-only rechecks, the sender calls \`TurnSessionStore.record_intent_checked(expected_state=..., operator_approved=True)\` once. Only if that private lock-protected durable write succeeds can the code execute exactly **one** synchronous, guarded button click. Every post-intent result is \`SUBMISSION_UNKNOWN\`, with \`sent=null\`; click return \`true\` is not server ACK, host/plugin identity, or proof of reply. Any rejected/failed/ambiguous click leaves the ledger UNKNOWN. A failed or ambiguous private write is \`INTENT_STATUS_UNKNOWN\` and sends nothing. No retry, no auto-resume, no EF02/Wake dispatch, no GitHub write, and no automatic ledger advance.

**After a future separately approved delivery:** Human verifies the UI response and an independently authentic GitHub comment, then a *different* explicit reconciliation operation may call \`observe_verified_reply\`. It is not bundled into one-shot Send. The host's independent plugin tool invocation remains UNPROVEN by a DOM chip, this CLI or a GitHub comment. Keep PR #187 DRAFT until reviewed and operator-Windows acceptance obtained. Never reuse old D2 UNKNOWN events #161/#177/#179.

**Testing:** Synthetic fake-page and store tests enforce disabled default, exact per-turn approval, stale source/route/state rejection, single exact chat/composer/expected role, durable UNKNOWN before click, no second click, and UNKNOWN on partial failure. These tests never open real Chrome or Send. CI results apply only to the candidate SHA actually tested.

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


## Human decision 2026-11-02 — CDP YES, Work NO

The Human selected exact-chat Chrome CDP 9333 and separate @Brain → @Engineering Workflow → @Brain turns. The Work event-triggered task was disabled. Historical UI 3/3 proof is retained, with no repeat requested. This candidate ports the already-reviewed private ledger/chip/one-shot sender source; it does NOT auto-enroll Chrome, grant browser Send, trigger EF02, prove native plugin invocation, or automatically infer a ChatGPT response. A new session/source/route and each actual Send remain separately consented, and UNKNOWN is never retried. The original PR #187 stays CLOSED and unmerged; this branch is the actual port for further implementation.
