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
