# EF02-D5.3 — Enrolled Issue registration and exact-run local trigger

Date: 2026-10-30. D5.3 baseline: merged D5.2 PR #121, source `9ff35b2936c45aeb43811bd4dabd9290ac6a7577`.

## Authority and operation

The entrypoint (`python -m ai_commerce.remote.local_wake_trigger`) runs one finite local session. An operator can supply an exact run ID as in D5.2, or explicitly enroll an Issue and its original immutable batch fingerprint to discover an exact run. `observe` is the default command. There is no service, scheduler registration, browser launch, automatic enrollment or arbitrary-chat posting API.

## Operator enrollment and discovery

The trusted local operator must retain the original dispatched request, know its issuer and actor, and provision a private directory outside both the operational checkout and this source checkout. Directory ACLs must limit access to the operator. The existing `PrivateJsonState` checks links/reparse points and uses OS locking plus atomic replace; it does not provision Windows ACLs. Use a distinct private registration file for each intent. Never put tokens, keys, chat URLs, route grants or private registration data in the repository, Issue, artifact or logs.

Compute the original request's fingerprint with `ai_commerce.remote.validation.fingerprint(request_dict)`: SHA256 of the full canonical JSON batch, including every action/attempt and instruction. A source SHA or batch ID alone is insufficient. This fingerprint must come from the operator's retained original request, not from an arbitrary later Issue body.

Explicit `--enroll` requires the hard-bound `trung-via/ai-commerce` repository, Issue, batch/source, workflow ID, run attempt, issuer, actor, original fingerprint and closed UTC creation window. A window spans at most 24 hours, ends before session start, and starts at most seven days before it. Local `--brain-issuer` entries may authorize a BRAIN issuer; Issue text never populates this allowlist. HUMAN still requires the repository owner.

The session reads authenticated repository owner, workflow ID/path and current Issue metadata/body. The current request and the exact run's immutable `ef02-request-<Issue>` archive must both match the retained fingerprint. An edited or missing original request blocks; REST metadata cannot reconstruct the original payload. Discovery lists only issue-triggered runs in that workflow/window, accepts a complete single page of at most `--max-runs` (default 50, maximum 100), and independently reads each exact run. Run title, list order and workflow HEAD never establish batch/source identity.

Adoption additionally authenticates the exact result archive, completion event and report, including Issue/run/attempt/batch/source and original action/attempt pairs. Multiple matching runs block. An unfinished or artifact-unready competitor prevents adoption. An authenticated workflow request snapshot for another Issue can be excluded. Any identity, archive or digest failure stops the session; it never silently scans a newer run after failure. An operator-supplied `--run-id` bypasses discovery while retaining all enrollment checks.

Only authenticated exact selectors, original-request fingerprint and minimal artifact/event provenance are persisted. Identical registration is idempotent. Reopening reauthenticates the saved exact run and refuses conflicting provenance or selectors; it never discovers a replacement. A registration is not a signed grant or delivery authority.

Example (placeholders are public selectors; provision the read-only token locally):

```text
python -m ai_commerce.remote.local_wake_trigger --enroll --repository trung-via/ai-commerce --issue-number <ISSUE> --batch-id <BATCH> --source-sha <40-CHAR-SHA> --workflow-id <EF02-WORKFLOW-ID> --run-attempt 1 --issuer-login trung-via --actor HUMAN --request-fingerprint <ORIGINAL-REQUEST-SHA256> --created-after <YYYY-MM-DDTHH:MM:SSZ> --created-before <YYYY-MM-DDTHH:MM:SSZ> --checkout <ABSOLUTE-CHECKOUT> --registration <PRIVATE-ABSOLUTE-REGISTRATION-JSON> --max-runs 50 --max-polls 20 --interval-seconds 30 --max-reads 200 --max-seconds 600 --read-attempts 3
```

Successful registration hands the authenticated event to the existing observation path and returns `registration_status=REGISTERED`, `status=CANDIDATE_READY_NOT_SENT`, `browser_accessed=false`. A finite snapshot cannot prove that no future or not-yet-indexed Issue trigger exists; discovery is deliberately bounded and conservative.

## Exact-run observation and optional delivery

1. Each poll fetches the pinned GitHub Actions run with a read-only operator-held token. The run must exactly match the repository, workflow, attempt, ID and event=issues. Other runs are never substituted.
2. After run status=completed, the poller selects exactly one unexpired ef02-results-{run_id}-{attempt} artifact associated with that run. A completed run without an uploaded artifact is considered not-ready. Duplicate/expired/wrong artifacts stop.
3. It downloads and validates the bounded ZIP, comparing the published digest if present, and reads only {batch_id}/completion_event.json and {batch_id}/batch_result.json. It validates event identity and source/batch/issue/run affinity. This data is only a candidate, not an independent Wake authority.
4. The default **observe** command returns CANDIDATE_READY_NOT_SENT and never attaches Chrome. The optional **deliver** command additionally requires Windows, an explicit AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED=true, signed consent/grants and operator-held HMAC key. It invokes D5.1 send_one, which independently reauthenticates GitHub content (D3.1), exact two grants (D3.2), route and idempotency (D2), then opt-in D4 CDP.
5. A single eligible candidate exits the polling loop, including if Send is UNKNOWN. It never blindly retries a possibly clicked doorbell. Observed UI user turn remains UNKNOWN and cannot prove ChatGPT server delivery or Brain continuation.

Polling bounds remain `max-polls=1..120`, `interval-seconds=1..600` (defaults 20/30). A shared GET budget additionally limits registration, polling, transient retries and independent D3.1 reads before optional delivery: `max-reads=1..5000` (default 200), `max-seconds=1..3600` (default 600), `read-attempts=1..5` (default 3). Counts/deadlines can stop the session before its poll limit. Production GET timeouts are capped at 15 seconds and the remaining session time; late responses are rejected. Tests inject clocks and sleeps.

Retries apply only to read-only HTTP 429/5xx, timeout errors and 403 with explicit rate-limit headers. Backoff doubles from one second, capped at 60; `Retry-After` or rate-limit reset hints are honored or the session blocks if they exceed its remaining budget. A rate-limit response without a reliable timing hint waits at least 60 seconds; a further increasing wait beyond the cap blocks. This follows [GitHub's rate-limit guidance](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#handle-rate-limit-errors-appropriately). Permanent 401/403, 404, identity mismatches, incomplete/duplicate lists, JSON/ZIP and digest errors stop immediately. Send/click calls are outside the retry policy.

## Non-sending check (operator must provision AI_COMMERCE_GITHUB_READ_TOKEN locally)

    python -m ai_commerce.remote.local_wake_trigger observe --run-id <ID> --run-attempt 1 --workflow-id <EF02-WORKFLOW-ID> --issue-number <ISSUE> --batch-id <BATCH> --source-sha <40-CHAR-SHA>

## Optional one-shot delivery after local enrollment and signed grants

    $env:AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED = "true"
    python -m ai_commerce.remote.local_wake_trigger deliver --run-id <ID> --run-attempt 1 --workflow-id <EF02-WORKFLOW-ID> --issue-number <ISSUE> --batch-id <BATCH> --source-sha <40-CHAR-SHA> --checkout "C:\TOOL\ai-commerce" --registry "C:\AI-Commerce-Private\routes.json" --idempotency "C:\AI-Commerce-Private\delivery.json" --grants "C:\AI-Commerce-Private\grants.json" --caller-id <trusted-operator-caller>
    Remove-Item Env:AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED -ErrorAction SilentlyContinue

This deliver invocation additionally requires an operator-owned AI_COMMERCE_WAKE_HMAC_KEY_HEX; its matching signed grants must be provisioned outside the Git working tree. The enrolled form uses the same enrollment arguments above plus explicit `deliver`, the Windows environment gate and these delivery options. Registration cannot mint or change grants, generations, route consent or caller identity. Never place tokens/keys/real chat URLs in Issues, repository, logs, or these examples. D5.1 describes private enrollment.

## Failure policy and remaining acceptance

Queued/running or missing artifact: bounded read-only polling until exhausted. Invalid or ambiguous evidence returns `BLOCKED`. Reopening a saved registration never clears durable delivery state. UNKNOWN from the one delivery invocation ends the session: D2 holds the event, and it must not be reset, given a new generation, or re-enrolled to force another click. A leftover registration `.lock` file is normal; its live OS lock, not file age, controls access. An interrupted atomic replace leaves no partial registration payload. Reconcile the original Issue/artifact and private state before a separately authorized recovery.

Focused tests use synthetic GitHub metadata/archives, clocks and browser/send substitutes. Windows executor checks do not prove Ubuntu execution, a real interactive Chrome session, an authenticated ChatGPT receipt or Brain continuation. No persistent automatic dispatch-to-local-session launcher is installed. Missing/expired request artifacts, absent original fingerprint, incomplete discovery, stale windows or competing unfinished runs are observable blockers. `LIVE_WAKE_NOT_PROVEN`.

Rollback after Human review: remove the D5.3 registration/retry wiring and its focused tests/docs; preserve private delivery records and grants for reconciliation. Candidate publication belongs to EF02; merge requires separate Human approval.
