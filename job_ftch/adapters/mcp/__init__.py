"""FastMCP multi-tenant server adapter.

Boundary: runtime adapter over ``TenantRunner`` only. No pipeline policy,
source parsing, or store semantics live here. Optional FastMCP import is
lazy inside ``TenantMCPServer`` so core install stays free of the ``mcp`` extra.
"""

from job_ftch.adapters.mcp.server import TenantMCPServer, create_server, probe_llm_backend

__all__ = ["TenantMCPServer", "create_server", "probe_llm_backend"]
