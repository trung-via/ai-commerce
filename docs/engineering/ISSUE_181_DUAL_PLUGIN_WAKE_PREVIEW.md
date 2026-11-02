# Issue #181 — three-route Wake test-chat preparation (non-delivering)

This is a **preview only**, not an update to the D2/D4/D5 production sender. It
preserves the existing `_doorbell_text` four-line message and `UNKNOWN`
idempotency. It does **not** alter or replay closed EF02 #179 or historical
#161/#177. Roadmap A stays paused. The separate plugin-MCP project (#182/
PR #183) remains stopped per Human decision.


## Third Wake route — Engineering-only when an action fails

Human selected an additional case: **if an EF02 action fails, Wake only
\`@AI Commerce Engineering Workflow\` to diagnose/fix; do not call Brain**.
This is an Engineering operating rule, not evidence that the host has selected
the plugin. Three distinct routes now exist:

| Final verified outcome / intent | Selected plugin(s) | Work |
| --- | --- | --- |
| EF02 success, Brain decision/sync wanted | Brain | Evidence Sync → decision |
| Human specifically requests Brain + Engineering together | Brain **and** Engineering | Brain research/handoff → Engineering review/authorized work → Brain decision |
| EF02 final \`PARTIAL_OR_FAILED\`, at least one action non-PASS | **Engineering only** | Exact failed action/attempt diagnosis → authorized Verify–Fix or bounded repair request |

**Precedence:** For any final batch with failed/non-PASS actions, the
Engineering-only failure route takes priority over success notifications
unless Human explicitly directs a different route. Nonterminal/pending/
unresolved workflow state must **not** be auto-promoted into a trusted
completed failure event. For a workflow that fails before producing an
authenticated completion artifact, the existing EF02 observer/GitHub Issue
evidence is a diagnostic fallback, NOT a fabricated D1 completion event.

The pure helper \`build_engineering_failure_wake_preview(event)\` validates
the complete D1 event contract, verifies \`PARTIAL_OR_FAILED\`, and lists
only non-PASS action/attempt/status tuples; it does **not** write to any
browser or GitHub resource. The event's digest is **not authentication**:
D3.1 authenticated GitHub artifact validation, signed D3.2 local operator
consent, exact chat binding, D2 write-ahead one-shot idempotency and a fresh
grant remain prerequisites to a **future** real Send.

The generated failure prompt contains exactly one
\`@AI Commerce Engineering Workflow\` and **no** \`@AI Commerce Brain\`.
Engineering first verifies logs/actual status, distinguishes code failure,
provider permission/quota, CI/test/environment, scope drift and unknown
side effects, then proposes/executes the *separately authorized* repair.
A status of \`EXECUTION_UNKNOWN\`, cancelled, timed out or ambiguous external
effect **must never result in blind retry**. Do not invent new EF02 attempts,
discard D2 UNKNOWN history or change executor/model without Human permission.
Brain is only reintroduced if the fix reveals a genuine product/roadmap/
architecture decision requiring Brain's authority.

Like the dual route, a literal \`@\` in generated text does **not** select the
ChatGPT plugin. A host-selected Engineering plugin must be verified; if
missing, STOP without auto Send and record \`ENGINEERING_PLUGIN_SELECTION_NOT_VERIFIED\`.

### Failure-route acceptance

- Verified final D1 failed event → one Engineering \`@\` mention, zero Brain
  \`@\` mentions, exact run/attempt/status, no Send
- Mixed batch → only non-PASS actions listed for Engineering triage
- Full success, tampered event, fake statuses or wrong repository → reject
- Missing plugin picker selection, stale chat, pre-existing draft, historical
  D2 UNKNOWN, unsupported origin/consent → do not auto Send
- After operator-authorized future E2E, distinguish UI turn, Engineering
  diagnosis, actual repair authorization/verified candidate and server ACK


## Preview and actual host distinction

Run from a reviewed checkout with a **fresh authorized** completion event ID:

```powershell
python -m ai_commerce.remote.wake_dual_plugin_preview --event-id <FRESH_EVENT_ID>
```

The output has one literal line with `@AI Commerce Brain` and
`@AI Commerce Engineering Workflow`, then a scoped same-turn Brain →
Engineering → Brain instruction. This does **not** select the installed
plugins: OpenAI's documented usage requires selecting them through ChatGPT's
`@` picker (or supported composer/plugin controls). Pasting plain `@` text
or `app://...` into a contenteditable via CDP cannot be promoted to a verified
host selection. A real host selection receipt/chip is distinct from a visible
plain-text mention and from reading the plugins' Skills.

## Safe test procedure (not executed by this patch)

1. On the operator's Windows Chrome, use the independently enrolled **Test Wake
   Chat**; check exact existing tab/route generation and private signed grant.
   Ensure an empty composer, no active generation and no duplicate wake turn.
2. For a **manual capability probe**, use the new preview only. In ChatGPT's
   `@` picker, explicitly choose both installed plugins in that one conversation;
   inspect the actual chosen UI chips before any Send. Do not assume pasted
   plaintext counts as chips. If the host cannot select both, STOP with
   `DUAL_PLUGIN_SELECTION_NOT_VERIFIED` and do not click Send.
3. A future automated Send needs a separately reviewed **host-supported,
   observable chip-selection mechanism**, a fresh never-attempted event and
   a separate one-shot operator grant. The legacy D4 `_INSERT` inserts only
   text and cannot establish this selection; do **not** attach this preview
   directly to that send path.
4. For a later authorized test, record only non-sensitive proof of exact chat,
   two real host-selected plugins, new event identity, Brain research/handoff,
   Engineering review and Brain final verdict. UI-level user turn remains
   `UNKNOWN / UI_USER_TURN_OBSERVED` absent independent server ACK.

## Negative acceptance cases

Wrong repository, invalid/fake event IDs, older UNKNOWN/replayed events,
missing plugin, one selected plugin, both mentions as plain text only, stale
route, pre-existing draft, active generation, wrong/duplicate chat tabs or
missing host selection proof must **not** trigger automated Send. No
account cookies, chat URLs, private profile contents or secrets are emitted.

## Current implementation and verdict

- **Delivered:** bounded preview builder and CLI, no browser/network/provider
  side effects; tests cover exact names, source identity and invalid input.
- **Not delivered:** automatic menu selection/host chip verification, live
  Test Wake Chat delivery, server ACK or a native plugin-to-plugin tool trace.
- **Next prerequisite:** operator-provided (redacted) UI evidence of both
  plugin chips in the test composer, or a documented host API for selecting
  these two plugins. Do not invent undocumented selectors or an MCP tunnel.
