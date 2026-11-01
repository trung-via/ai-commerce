# Issue #181 — Read-only ChatGPT plugin chip reconnaissance for D5

## Status and scope

This is an **operator-gated, attach-only diagnostic component** in a PR branch.
It does NOT automatically select plugins, does NOT click or type in the
browser, does NOT route EF02 events, and does NOT integrate with the D2/D4/D5
production Send path. It must not be used to claim a live E2E Wake.

The Human supplied two screenshots of a single registered Test Wake Chat:
one composer displayed two blue plugin chips (Brain + Engineering), another
displayed only Engineering. Both were unsent. These prove **manual UI
selection is feasible**, not an authenticated host tool invocation or an
automatable DOM contract. The actual DOM attributes/selectors are still
unobserved.

## What was implemented

\`src/ai_commerce/remote/plugin_selection_probe.py\` exposes
\`ReadOnlyPluginSelectionProbe(enabled=False).inspect(binding)\`.
It accepts **only** an existing \`ResolvedBinding\` for an exact known
\`chatgpt.com\` conversation, plus a loopback CDP endpoint. It attaches to
existing Chrome through the same Playwright inventory used by D4, never
launches a new Chrome/window, never navigates, never clicks/type/sends,
never inspects another tab's message body, and never returns the chat URL,
conversation messages, cookies, credentials or raw DOM.

The probe returns only allowlisted diagnostic codes and two state fields,
one per known plugin:

| Diagnostic | Meaning | Can authorize Send? |
| --- | --- | --- |
| \`ABSENT\` | No exact plugin label element in current composer form | **NO** |
| \`PLAIN_TEXT_ONLY\` | Label visible but without a recognizable noneditable/mention structure | **NO** |
| \`STRUCTURED_CANDIDATE\` | Visible exact label with likely mention/chip structure | **NO** |
| \`AMBIGUOUS\` | Multiple candidate matching labels in current composer | **NO** |
| \`PROBE_COMPLETE\` | Read-only scan finished | **NO** |
| \`DRAFT_PRESENT\` (legacy D4) | Existing staged content blocks the old Wake sender | **NO** |

**Critical invariant:** \`host_selection_proven\` is hard-coded false.
An observed \`STRUCTURED_CANDIDATE\` is NOT equivalent to a host-verified,
installed, selected plugin and cannot become a bypass for D4's exact
composer/Send gate.

## Operator test, bounded and no Send

1. Use the **existing registered Test Wake Chat** in the separately
   enrolled Windows Chrome; ensure the exact route/generation is correct.
2. On a **read-only, opt-in diagnostic call** using the existing privately
   resolved \`ResolvedBinding\`, exercise \`inspect(binding)\` once with:
   - Both Brain and Engineering selected as actual UI chips;
   - Engineering-only selected as an actual UI chip;
   - A plain-text \`@AI Commerce Brain\` / \`@AI Commerce Engineering Workflow\`
     draft without the picker, as a negative control.
   Never click Send. Clear the entire test draft/chips after each check.
3. Record only the code and two allowlisted states; never print or upload
   private route URLs, CDP endpoints, screenshots, DOM dumps, browser
   profiles or tokens. A project/local Windows adapter entrypoint that
   provides the already-authorized binding may be added after review.
   **A read-only local CLI is now available** and accepts an operator-held private enrolled registry, explicit route ID, repo root, and `--read-only` opt-in. It is not a Wake sender and does not require an EF02 completion event.

   On the operator's own Windows PowerShell, from the reviewed repository checkout:

   ```powershell
   python -m ai_commerce.remote.plugin_selection_probe_cli --registry "<PRIVATE_D5_REGISTRY_ABSOLUTE_PATH>" --checkout "." --route-id "<EXISTING_REGISTERED_ROUTE_ID>" --read-only
   ```

   Read only the returned safe JSON fields `code`, `brain`, `engineering` and `host_selection_proven`. That last flag is **always false**. Do not log or post the private registry, route URL, CDP endpoint, browser profile or authorization material. No Send is possible through this CLI.

4. If any result is absent, ambiguous, plain-text only, or unexpectedly
   different on the real host, stop; do not guess selectors. Separate
   review/authorization is required before developing host-aware plugin
   selection (which the current code does not implement).

## Three planned routing modes remain distinct

- Final **PASS**: Brain-only (existing D4 selector-only Wake shown at UI).
- Human-selected joint task: Brain + Engineering in one user turn.
- Final **PARTIAL_OR_FAILED**: Engineering-only, verify before authorized
  Verify–Fix; unknown-side-effect attempts are never blindly retried.

Only the **preview** functions from merged PR #184 generate these prompts.
\`LocalChromeDoorbellAdapter\` still sends its minimal four-line selector,
without @ plugin selection. A future true E2E Send requires independently
authenticated D3 GitHub artifact provenance, signed local operator consent,
unique exact origin binding, fresh single-use D2 grant, and an observed
**host-supported plugin-selection mechanism**. Screenshot proof of manual
selection does not fulfill these automatic requirements.

## Verification

The new regression suite validates default-disabled behavior, exact-chat
and duplicate-tab rejection, output sanitization, invalid binding
rejection, and synthetic DOM cases including real-looking chips, a single
Engineering chip, plain text and duplicate tokens. Hosted CI must verify
the exact PR head on Ubuntu and Windows. Do not assert those checks passed
until their actual run completes.

**No D5 Send, no EF02 dispatch, no plugin MCP connection, no merge, no retry
of historical D2 UNKNOWN #161/#177/#179.** Roadmap A remains paused.
