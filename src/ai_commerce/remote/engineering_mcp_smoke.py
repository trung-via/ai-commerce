"""Local MCP v2 contract smoke test for Issue #182 — no network or Wake.

Run after installing optional mcp>=2,<3:
    python -m ai_commerce.remote.engineering_mcp_smoke

This proves an SDK client can list/invoke this local server. It does NOT prove
the ChatGPT host loaded the plugin or that a browser Wake was delivered.
"""
from __future__ import annotations

import asyncio
import json

from . import engineering_mcp_server as adapter

_EXPECTED = "engineering_review_readonly"


async def smoke() -> dict:
    # Delayed import: no SDK dependency on ordinary AI Commerce operations.
    from mcp import Client

    invocations: list[tuple[str, int, str, str | None]] = []

    def fake_review(repository, pr_number, correlation_id, expected_main_sha=None):
        invocations.append((repository, pr_number, correlation_id, expected_main_sha))
        return {
            "verdict": "SMOKE_LOCAL_ONLY",
            "chat_delivery_proven": False,
            "native_plugin_handoff_proven": False,
        }

    # Temporarily replace GitHub-facing logic so this smoke has no HTTP, token,
    # browser or dispatch path, even if the operator environment has credentials.
    original = adapter.engineering_review_readonly
    adapter.engineering_review_readonly = fake_review
    try:
        server = adapter.build_mcp_server()
        async with Client(server, raise_exceptions=True) as client:
            result = await client.list_tools()
            matches = [t for t in result.tools if t.name == _EXPECTED]
            if len(matches) != 1:
                raise RuntimeError("MCP_TOOL_NOT_REGISTERED")
            tool = matches[0]
            if not (tool.annotations is not None and
                    tool.annotations.read_only_hint is True):
                raise RuntimeError("MCP_READ_ONLY_ANNOTATION_MISSING")
            schema = tool.input_schema
            if (not isinstance(schema, dict) or
                    schema.get("type") != "object" or
                    not {"repository", "pr_number", "correlation_id"}
                        <= set(schema.get("properties", {}))):
                raise RuntimeError("MCP_SCHEMA_INVALID")
            reply = await client.call_tool(_EXPECTED, {
                "repository": "trung-via/ai-commerce",
                "pr_number": 180,
                "correlation_id": "issue182-smoke",
            })
            if reply.is_error:
                raise RuntimeError("MCP_LOCAL_CALL_ERROR")
            if invocations != [
                ("trung-via/ai-commerce", 180, "issue182-smoke", None)
            ]:
                raise RuntimeError("MCP_LOCAL_CALL_NOT_INVOKED")
    finally:
        adapter.engineering_review_readonly = original
    return {
        "status": "MCP_LOCAL_SDK_SMOKE_PASS",
        "tool": _EXPECTED,
        "local_tool_invocations": len(invocations),
        "github_accessed": False,
        "chat_delivery_proven": False,
        "native_plugin_handoff_proven": False,
    }


def main() -> None:
    print(json.dumps(asyncio.run(smoke()), sort_keys=True))


if __name__ == "__main__":
    main()
