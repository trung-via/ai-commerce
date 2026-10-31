"""Optional MCP v2 stdio adapter for #182's read-only engineering review.

Local SDK import is delayed until server startup. This is not a ChatGPT-installed
MCP action until an authenticated, supported host connects the server.
"""
from __future__ import annotations

from .engineering_review_tool import engineering_review_readonly


def build_mcp_server():
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer("AI Commerce Engineering Review")

    @server.tool(
        name="engineering_review_readonly",
        title="Review AI Commerce PR and post-merge CI",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    def engineering_review_readonly_mcp(
        repository: str, pr_number: int, correlation_id: str,
        expected_main_sha: str | None = None,
    ) -> dict:
        """Read-only GitHub review. Cannot dispatch EF02, send Wake, or merge."""
        return engineering_review_readonly(
            repository, pr_number, correlation_id, expected_main_sha)

    return server


def main() -> None:
    build_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
