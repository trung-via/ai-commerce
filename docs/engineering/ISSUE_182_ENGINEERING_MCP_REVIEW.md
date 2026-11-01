# Issue #182 — Wake-first Engineering MCP Review (W1)

**Priority**: Human paused Roadmap A product work to complete Wake. This PR adds a bounded read-only Engineering review tool, **not** a new EF02 event, ChatGPT send, merge or plugin deployment.

## Source and authority

- `src/ai_commerce/remote/engineering_review_tool.py` reads GitHub from a fixed repository (`trung-via/ai-commerce`), with strict identity and expected-main-SHA checks. GET-only GitHub API, no redirect following, bounded response size/timeout, fail-closed source checks. `AI_COMMERCE_GITHUB_READ_TOKEN` is supplied by the trusted operator environment and never included in the output.
- `src/ai_commerce/remote/engineering_mcp_server.py` exposes an MCP v2 `stdio` tool `engineering_review_readonly` with `read_only_hint=True`. Requires a separately installed Python MCP SDK (v2); its import is delayed to preserve the standard AI Commerce package install.
- The tool returns GitHub PR, branch, check-status evidence, a local trace ID, an observation timestamp and explicit verdict. The trace ID **does not** prove host-level MCP selection or exact-chat server receipt. It always reports `chat_delivery_proven=false` and `native_plugin_handoff_proven=false`. Duplicate correlation IDs can safely repeat read requests; they do not claim durable exactly-once delivery.

## Local setup (only in a host that supports stdio MCP)

```powershell
python -m pip install -e ".[dev]"
python -m pip install "mcp>=2,<3"
# Set AI_COMMERCE_GITHUB_READ_TOKEN privately in the trusted operator environment
python -m ai_commerce.remote.engineering_mcp_server
```

The final process waits for MCP requests. Do not paste tokens in a public repo, Issue, plugin manifest or chat. An HTTP/ChatGPT connection is **not** created by this command; web ChatGPT requires a real supported MCP endpoint/connection, not an invented URL. An operator may need an actual hosted MCP server or supported secure tunnel. Do not treat Skills as callable MCP tools.

## Scope and verification

- Modified only two new `remote/` Python modules and added regression cases to the **existing registered** `tests/test_ef02_d5_local_handoff.py` family; no new MT-01 test family, protected `.github` file or runner configuration.
- Tests cover success, wrong repo/PR, stale SHA, unmerged PR, mismatched commit, failed/pending/missing/incomplete checks, missing authorization token, GitHub transport/authorization errors, repeat-read correlation, and absence of fabricated host receipt/side effects.
- Local isolated review-core verification: **19 passed**. GitHub hosted Ubuntu/Windows CI must verify the exact PR head before any merge decision.
- Fail closed if the merged PR commit no longer matches current main HEAD; this first tool is intended for the exact #180 acceptance scenario, not arbitrary historical PRs.

## Remaining gates, not claimed

1. Review PR, CI and ownership. No main merge without a separate Human instruction.
2. Deploy/connect a **real** supported MCP tool to the existing Engineering Workflow plugin (preserve plugin identity), then record host tool invocation in the same ChatGPT conversation without Human selecting Engineering in a second message. Do not claim `CROSS_PLUGIN_HANDOFF_PROVEN` from source/CI/Skill selection.
3. After a separately authorized **new** completed event, perform the Wake→Brain→Engineering→Brain test only with a fresh operator grant and event-specific permissions. Never retry #179, #177, #161 D2 UNKNOWN.
4. Issue #109 independently tracks server-authenticated original-chat delivery receipt. GitHub Issue/artifact remains the fallback; Chrome UI user-turn observation does not satisfy server ACK.

**Current state**: implemented candidate/PR and local isolated tests; MCP host connection and full Wake cycle **NOT PROVEN**.


## Supported private host connection (operator runbook, not executed)

The code in this PR provides an **MCP v2 stdio server only**. This is suitable
for a local MCP host or, **if the user's OpenAI Platform organization and ChatGPT
workspace have Secure MCP Tunnel access**, a private tunnel. It is *not* a
public HTTPS MCP endpoint and its Python module cannot be called directly from
this ChatGPT web chat. SDK v2 reference:
https://py.sdk.modelcontextprotocol.io/ and
https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md.

1. On the operator-owned Windows machine, check out the exact reviewed candidate
   SHA, create a Python 3.11+ environment and install the project plus
   `mcp>=2,<3`. Keep the GitHub read-only token in a **private environment
   variable** `AI_COMMERCE_GITHUB_READ_TOKEN`, never in the repository or
   a ChatGPT prompt. The tool has no GitHub write path.
2. Verify local MCP initialization, list-tools and a read-only call with the
   MCP Inspector or a v2 SDK client. The target module is
   `ai_commerce.remote.engineering_mcp_server` and the stdio invocation is
   `python -m ai_commerce.remote.engineering_mcp_server`.
   Check the actual annotation `readOnlyHint=true`, the input schema and
   returned `MERGED_MAIN_POSTMERGE_CI_PASS` for PR #180 with the *current*
   main SHA. Repeated calls must have no side effects.
3. **Only if Secure MCP Tunnel is available and authorized**: create/choose
   a tunnel in Platform tunnel settings; on the same private machine run the
   official `tunnel-client` with its `--mcp-command` pointing to the
   executable + module. Run `tunnel-client doctor` and confirm tunnel
   readiness. See:
   https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
   for current profile syntax and eligibility. Do not fabricate a tunnel ID.
4. In **ChatGPT Plugins**, choose Add custom MCP server, select Tunnel
   connection and the actual available tunnel, authenticate as required and
   test one real `engineering_review_readonly` call. A cloud-hosted
   alternative needs a genuinely reachable, authenticated Streamable HTTP
   endpoint; this PR intentionally does not expose the local GitHub token
   over a public HTTP listener. The current skills-only Engineering
   Workflow plugin does **not** gain a callable tool merely by appending
   an instruction or writing `capabilities: ["Interactive"]`.
5. Verify the host tool call trace independently. A separately created
   custom MCP connection is not yet an attachment to the pre-existing
   `AI Commerce Engineering Workflow` plugin identity; first confirm the
   platform can associate the tested connection with that same plugin.
   If it cannot, record this packaging/host limitation and do not claim
   successful native plugin-to-plugin delegation under Issue #181.

**Readiness evidence to record**: exact server revision, actual MCP SDK
version, list-tools tool schema/annotations, auth/denial behavior, real
connection/tunnel identifier (in private operator state; no secrets),
ChatGPT host-visible tool invocation, exact GitHub PR/main/CI source, and
Brain's post-review decision. A local model-generated trace ID is never
a ChatGPT server ACK or proof of an independently invoked plugin.

The ChatGPT user may not have access to Secure MCP Tunnel; in that case
report `BLOCKED_TUNNEL_ACCESS` and use a supported authenticated hosting
alternative. Never bypass private plugin/host permission checks, and never
replay historical Wake events #161/#177/#179.

## Local MCP SDK smoke (without GitHub/ChatGPT side effects)

After installing `mcp>=2,<3` in the operator's **own Python environment**
and checking out this PR's current SHA, run:

```powershell
python -m ai_commerce.remote.engineering_mcp_smoke
```

The probe uses a real `mcp.Client(server)` SDK client **in-process** to
list the tool definition, check the read-only annotation and input schema,
and invoke one synthetic tool call. It replaces the GitHub-bound function
during the probe, so it never reads the local GitHub token or sends HTTP,
Wake, Chrome actions or EF02 work. Expected terminal marker:

`"status": "MCP_LOCAL_SDK_SMOKE_PASS"`

This is **local SDK interoperability evidence only**. If it fails, record
the SDK version and sanitized exception without publishing credentials.
If it passes, continue with the separately required tunnel readiness
and **actual ChatGPT host-visible invocation**. Do not mark #181 or #109
accepted based on this output.
