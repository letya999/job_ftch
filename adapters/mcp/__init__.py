"""FastMCP multi-tenant server adapter."""

from adapters.mcp.server import TenantMCPServer, create_server

__all__ = ["TenantMCPServer", "create_server"]
