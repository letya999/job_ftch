"""FastMCP multi-tenant server adapter."""

from job_ftch.adapters.mcp.server import TenantMCPServer, create_server, prepare_stdio_logging

__all__ = ["TenantMCPServer", "create_server", "prepare_stdio_logging"]
