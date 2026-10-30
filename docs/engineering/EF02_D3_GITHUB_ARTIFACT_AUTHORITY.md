# EF02-D3.1 — Verified GitHub Artifact Authority

Issue: [#109](https://github.com/trung-via/ai-commerce/issues/109).
Built on merged D2 (PR #105). **This package authenticates source evidence; it does not send ChatGPT messages or create native ChatGPT Wake.**

## Supported capability after this change

`GitHubRestArtifactAuthority` implements the D2 `ArtifactAuthority` protocol.

- Uses an **operator-injected token provider** at the trusted local composition root. No token is read from an Issue, completion event, LLM output, GitHub artifact, command argument or repo file.
- Calls the actual GitHub REST `actions/runs/{run_id}` and `actions/runs/{run_id}/artifacts` APIs over HTTPS with the token; pins the configured repository, workflow ID, event type (`issues`), completed run, run attempt, and exact artifact name `ef02-results-{run_id}-{run_attempt}`.
- Downloads the run's actual archive by artifact ID. HTTPS redirect to a signed artifact URL explicitly **strips authorization credentials**. Proxy environment variables are ignored by the local fetcher. Data sizes are bounded.
- Requires exactly one root `<batch_id>/completion_event.json` and `<batch_id>/batch_result.json` within the authenticated archive. Strict duplicate-key decoding and exact value equality reject inconsistent or fabricated event and final report data.
- Compares GitHub published SHA-256 artifact digest (when reported), and an explicitly supplied digest (if present), against fetched archive bytes. A public digest **alone** never grants authority.
- Fails closed with `False` for unavailable GitHub API, missing/incorrect token, partial run, wrong workflow, wrong run attempt, expiry, malformed archive, zip-bomb-size excess or any mismatch. Does not leak transport exception messages or raw tokens.

## Composition root example (documentation only)

```python
import os
from ai_commerce.remote import GitHubRestArtifactAuthority

artifact_authority = GitHubRestArtifactAuthority(
    repository="trung-via/ai-commerce",
    workflow_id=399111264,  # observed workflow ID for EF02 Issue #100; revalidate on deployment
    token_provider=lambda: os.environ["AI_COMMERCE_GITHUB_READ_TOKEN"],
)
# Inject into deliver_exact_chat_wake(..., artifact_authority=artifact_authority)
# A separate independently trusted OriginAuthority is still REQUIRED.
```

The operator must provision a minimal read-only GitHub Actions token via their trusted local credential store or process environment. Do not record its value in Issues, CI logs, Git commits or chat. This package does **not** set, generate, query or grant tokens. Run the integration only through an authorized trusted local service.

## Tests and remaining boundaries

Deterministic tests use mocked GitHub HTTP responses and ZIP archives, with mismatched provenance, duplicate JSON keys, wrong workflow/run/attempt/repository/artifact, missing token and signed-CDN redirect credential stripping. Actual authenticated GitHub calls require user-operated credentials and have **not** been performed by tests. CI Windows/Ubuntu is the integration verification.

The class authenticates the *artifact* side of D2. It does **not** implement a production local caller/consent `OriginAuthority`, a host-supported write channel to an existing ChatGPT conversation, an exact-turn receipt or Brain continuation. D2's fail-closed `UNKNOWN/UNSUPPORTED` status and GitHub Issue/Actions fallback remain unchanged.

For supported future delivery paths, check primary sources:
- [ChatGPT Plugin Extensions](https://developers.openai.com/plugins/build/extensions): extended sending is described for **current/new** conversation in supported desktop/Work UI; no promise of arbitrary existing chat background posting.
- [ChatGPT scheduled/event-triggered tasks](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt): eligible GitHub PR event triggers can create **Work task runs**, which are not automatically the user's original conversation.

Do not use private ChatGPT endpoints, cookie scraping, unverified CDP clicks, or auto-new-chat fallback. Issue #109 tracks separately gated D3.2–D3.4.

## Rollback

Revert this independent module, tests, documentation and export. It changes no EF02 scheduler, Git hook, runner permissions, GitHub secrets, event payload or D2 transport defaults.
