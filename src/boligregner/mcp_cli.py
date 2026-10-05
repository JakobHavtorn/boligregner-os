"""CLI entry point for the boligregner MCP server.

Runs the server over stdio (the default transport for MCP servers), so an AI
client such as Claude Desktop or any MCP-compatible agent can launch it via::

    python -m boligregner.mcp_cli

or, once installed, via the ``boligregner-mcp`` console script.
"""

from __future__ import annotations

import argparse

from .mcp_server import mcp


def main() -> None:
    """Run the boligregner MCP server over stdio."""
    parser = argparse.ArgumentParser(
        prog="boligregner-mcp",
        description=(
            "Run the boligregner-os MCP server, exposing the "
            "calculate_mortgage tool to AI agents."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport protocol (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for HTTP transports (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for HTTP transports (default: 8000).",
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    elif args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
