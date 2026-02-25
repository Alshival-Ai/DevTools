"""Alshival MCP server package.

Extends package lookup so local ``mcp`` code can coexist with the external
``mcp`` SDK (which provides ``mcp.server.fastmcp``).
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
