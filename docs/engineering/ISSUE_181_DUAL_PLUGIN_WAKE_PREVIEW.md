# Issue #181 — two-@ Wake test-chat preparation (non-delivering)

This is a **preview only**, not an update to the D2/D4/D5 production sender. It
preserves the existing `_doorbell_text` four-line message and `UNKNOWN`
idempotency. It does **not** alter or replay closed EF02 #179 or historical
#161/#177. Roadmap A stays paused. The separate plugin-MCP project (#182/
PR #183) remains stopped per Human decision.

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
