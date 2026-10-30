# EF02-D5.1 — Windows operator-gated local CDP Wake entry

Status: engineering candidate only. Tracks [Issue #109](https://github.com/trung-via/ai-commerce/issues/109), based on the merged D1–D4 at `main@1b376fb987bfca2577f718db8ce8ddf450e30f0d`.

## What is implemented

`python -m ai_commerce.remote.local_wake_entry` is an **explicit local invocation** seam between EF02 output and the existing D3.1 GitHub artifact verifier, D3.2 signed consent, D2 idempotent delivery and D4 opt-in Chrome CDP adapter. This is **not** a watcher/doorbell daemon, GitHub workflow auto-trigger, Windows service, browser/profile launcher, ChatGPT Work task or native Brain continuation.

The source of truth for an EF02 result is the immutable GitHub run + its `ef02-results-<run_id>-<run_attempt>` artifact. D5 reads the user's already-downloaded, potentially untrusted `completion_event.json` and `batch_result.json` files. D3.1 independently fetches the GitHub run and actual artifact and rejects any different bytes before D4 is invoked. Do not choose the newest available artifact: use a **specific** authorized run, attempt and batch.

## Operator-only steps for Windows

These are instructions for the authorized local user, **not actions run by this PR**.

1. Use a Windows *interactive* Python process under the intended local user. Ensure Chrome is already running with the previously authorized CDP endpoint and the exact ChatGPT regular conversation tab open; this component never starts/navigates Chrome or creates a tab. Install project and optional CDP dependency: `python -m pip install -e ".[local-chat-wake]"`.
2. From the target GitHub EF02 run, download its exact `ef02-results-<run_id>-<attempt>` ZIP and extract to a **local private folder**. Identify `<batch_id>/completion_event.json` and `<batch_id>/batch_result.json` inside it. Do not edit. Its source is not trusted until GitHub verification.
3. In a **separate operator-private directory outside the Git checkout**, enroll the exact original chat/route, generation and matching batch claim using existing `LocalOriginRegistrar` with a Human-authorized issuer. The registry and idempotency state paths must be absolute and outside the repo. Do **not** copy AIOS credentials or profiles. A matching batch claim and signed grants cannot be inferred from an Issue or a chat URL alone.
4. Provision a private `grants.json` file there with `{"version":1,"grants":[ ... ]}`. These must be **operator-issued HMAC-SHA256 signed grants** with `human_consent=true`, the exact current registry digest and exactly the relevant `resolve_claim_route` and `deliver_wake` operation scopes. This command does **not** issue grants, make a consent decision or generate a key. Use the already merged D3.2 contract (`signed_origin_consent.py`) and the authenticated principal Windows SID obtained from the actual process token. Store signing material in a local operator-owned secret store, not in GitHub, Issue comments, batch artifacts or this file.
5. Supply `AI_COMMERCE_GITHUB_READ_TOKEN` with **GitHub Actions read** permission on this repository, and `AI_COMMERCE_WAKE_HMAC_KEY_HEX` with the hex-encoded operator-owned HMAC key. The same key must have signed the grants. Do not type or print secret literals into scripts that will be committed or pasted into chat.
6. Run the **offline precheck** (never attaches Chrome):

```powershell
python -m ai_commerce.remote.local_wake_entry check `
  --checkout "C:\\TOOL\\ai-commerce" `
  --event-file "C:\\AI-Commerce-Private\\results\\completion_event.json" `
  --result-file "C:\\AI-Commerce-Private\\results\\batch_result.json"
```

`PRECHECK_ONLY` confirms merely that locally supplied files have a valid event shape and matching minimal report fields. It **does not authenticate GitHub or verify a chat**.

7. Only after completing and reviewing the binding/grants, opt in to **one single explicit local invocation**, not a general CI runner:

```powershell
# Set private credentials via your trusted local secret provider, not literal example values
$env:AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED = "true"
python -m ai_commerce.remote.local_wake_entry send `
  --checkout "C:\\TOOL\\ai-commerce" `
  --event-file "C:\\AI-Commerce-Private\\results\\completion_event.json" `
  --result-file "C:\\AI-Commerce-Private\\results\\batch_result.json" `
  --registry "C:\\AI-Commerce-Private\\routes.json" `
  --idempotency "C:\\AI-Commerce-Private\\delivery.json" `
  --grants "C:\\AI-Commerce-Private\\grants.json" `
  --caller-id "<operator-enrolled-caller-id>" `
  --workflow-id <actual-EF02-workflow-id>
Remove-Item Env:AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED -ErrorAction SilentlyContinue
```

The workflow ID must come from the **actual GitHub EF02 run metadata** (the `workflow_id` of the `.github/workflows/ef02-batch.yml` workflow). Do not substitute the run ID. Replace illustrative paths with actual absolute paths, never use a repository-owned registry/grants. Keep Chrome profile/port authorization managed locally; no default browser launch.

## Semantics and recovery

- `check` returns `0` only for **PRECHECK_ONLY**. No browser/network side effects and no conclusion about provenance.
- `send` is blocked unless Windows + a literal `AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED=true` + both credentials + signed matching grants + current registry + genuine GitHub artifact evidence. It requires an explicit CLI command. No executor or CI workflow is configured to run it.
- The code gets the caller principal from the **Windows process token SID** (not user-controlled environment variables). That SID must match the signed grant principal; a Windows service account or unrelated session will not implicitly inherit the Human's consent.
- D2 writes `UNKNOWN` to disk before D4 can click Send, keyed by `event_id :: route_id :: generation`. After a browser-observed exact user turn, D4 returns `UNKNOWN / UI_USER_TURN_OBSERVED`; no authenticated server acknowledgement exists. It is **not safe to interpret command exit as confirmed Wake**. The command deliberately returns nonzero for any unproven send.
- No automatic retries after `UNKNOWN`, including crashes after click. On `UNSUPPORTED`, repeated invocation uses the existing idempotency record rather than blindly sending.
- The practical recovery path is operator reconciliation against the **exact original conversation and event** plus GitHub result; do not delete the idempotency file, increment generation or mint a new event merely to force a resend.
- Never allow this process to choose the latest chat, read assistant output, claim `brain_continuation_proven`, invoke EF01/EF02, or merge a PR. The doorbell only asks Brain to **freshly sync** the verified GitHub event.

## Acceptance boundary and future work

This PR can establish **D5.1 local wiring** with deterministic tests and CI. It cannot prove that the Human's interactive Windows Chrome is accessible from GitHub Actions, nor that ChatGPT received/processed the doorbell. Remaining D5 tasks: an authorized event-to-local wake trigger (without broad Work watchers), local grant enrollment and Chrome interactive conformance, explicit proof of the doorbell in the correct chat, Brain response/continuation evidence and bounded reconciliation for busy/uncertain cases. These must be evidenced separately; retain `LIVE_WAKE_NOT_PROVEN` until then.

Rollback: revert this new entrypoint, tests and documentation. No workflow, runner, Playwright default, GitHub permission, private registry or credential store is modified by this candidate.
