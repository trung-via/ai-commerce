# Wake Control Center (#200) — local UI candidate

This standalone modern status dashboard uses Python's standard library only.
It connects only to \`127.0.0.1:8765\`, never publishes private routes or opens
external services. It is stacked upon PR #198 and is NOT merged/activated.

On the operator's Windows checkout, after separately authorized activation:

\`\`\`powershell
python -m ai_commerce.remote.wake_control_center --checkout <CHECKOUT> --state-file <PRIVATE_LEDGER> --registry <PRIVATE_ROUTE_REGISTRY> --route-id <ENROLLED_ROUTE>
\`\`\`

Open \`http://127.0.0.1:8765\` on that Windows host. UI shows ledger phase,
Brain → Engineering → Brain, private-route consent and TCP reachability of
CDP 9333 (NOT Chrome ownership proof). Clicking **Mở chat chính xác** invokes
the existing exact-chat preload only when READY, reachable and enrolled.
The browser request is limited to same-origin POST, exact Host and an ephemeral
session token. Ambiguous navigation disables repeat attempts within the UI.

The UI has **no Send, Prepare, automated Wake, GitHub writes, automatic retry,
browser profile management or new entitlement**. Existing \`prepare-turn\`,
\`send-once\`, \`observe-reply\` CLI remain separate and require their own
Human approval; no ChatGPT server ACK or native plugin invocation is claimed.
CI uses fake inventory and pure status tests; Chrome live activation is separate.
