# EF02-D5.2 — Pinned GitHub run → local CDP doorbell trigger

Date: 2026-10-30. Baseline: merged D5.1 main@89c3a09cb476f3b6a7080ec609631cafc470c3cd.

## Authority and operation

The D5.2 entrypoint (python -m ai_commerce.remote.local_wake_trigger) is a bounded **local exact-run poller**, not a GitHub Work task, a CI Windows service, a browser launcher or a latest-chat selector. It requires one operator-selected run_id, run_attempt, workflow_id, issue_number, batch_id and source_sha. No automatic discovery or arbitrary existing-chat posting API is used.

1. Each poll fetches the pinned GitHub Actions run with a read-only operator-held token. The run must exactly match the repository, workflow, attempt, ID and event=issues. Other runs are never substituted.
2. After run status=completed, the poller selects exactly one unexpired ef02-results-{run_id}-{attempt} artifact associated with that run. A completed run without an uploaded artifact is considered not-ready. Duplicate/expired/wrong artifacts stop.
3. It downloads and validates the bounded ZIP, comparing the published digest if present, and reads only {batch_id}/completion_event.json and {batch_id}/batch_result.json. It validates event identity and source/batch/issue/run affinity. This data is only a candidate, not an independent Wake authority.
4. The default **observe** command returns CANDIDATE_READY_NOT_SENT and never attaches Chrome. The optional **deliver** command additionally requires Windows, an explicit AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED=true, signed consent/grants and operator-held HMAC key. It invokes D5.1 send_one, which independently reauthenticates GitHub content (D3.1), exact two grants (D3.2), route and idempotency (D2), then opt-in D4 CDP.
5. A single eligible candidate exits the polling loop, including if Send is UNKNOWN. It never blindly retries a possibly clicked doorbell. Observed UI user turn remains UNKNOWN and cannot prove ChatGPT server delivery or Brain continuation.

Polling bounds: max-polls=1..120 and interval-seconds=1..600; defaults 20 checks with 30 seconds between checks. A separate trusted issuer-to-local run registration/orchestrator is still needed to start this per-run session after an EF02 dispatch. This PR is not a persistent multi-run daemon.

## Non-sending check (operator must provision AI_COMMERCE_GITHUB_READ_TOKEN locally)

    python -m ai_commerce.remote.local_wake_trigger observe --run-id <ID> --run-attempt 1 --workflow-id <EF02-WORKFLOW-ID> --issue-number <ISSUE> --batch-id <BATCH> --source-sha <40-CHAR-SHA>

## Optional one-shot delivery after local enrollment and signed grants

    $env:AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED = "true"
    python -m ai_commerce.remote.local_wake_trigger deliver --run-id <ID> --run-attempt 1 --workflow-id <EF02-WORKFLOW-ID> --issue-number <ISSUE> --batch-id <BATCH> --source-sha <40-CHAR-SHA> --checkout "C:\TOOL\ai-commerce" --registry "C:\AI-Commerce-Private\routes.json" --idempotency "C:\AI-Commerce-Private\delivery.json" --grants "C:\AI-Commerce-Private\grants.json" --caller-id <trusted-operator-caller>
    Remove-Item Env:AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED -ErrorAction SilentlyContinue

This deliver invocation additionally requires an operator-owned AI_COMMERCE_WAKE_HMAC_KEY_HEX; its matching signed grants must be provisioned outside the Git working tree. Never place tokens/keys/real chat URLs in Issues, repository, logs, or these examples. D5.1 describes private enrollment.

## Failure policy and remaining acceptance

Queued/running: read-only bounded polling. Missing artifact: wait until bounded exhaustion. Run, artifact, issue, batch, source or ZIP mismatch: fail closed. Unknown click/reply: D2 holds the event; do not reset the idempotency file or choose a new generation to force resend. Github Issue/artifact remains the authoritative fallback.

All tests use mock GitHub and browser inputs. No real Windows interactive Chrome session, profile, token or ChatGPT message was exercised in CI. Remaining D5 requirements are operator enrollment, reliable run-to-local launch registration, live UI evidence, independent Brain continuation proof and bounded recovery. LIVE_WAKE_NOT_PROVEN.

Rollback: revert new trigger, tests and document; D1–D5.1 remain intact.