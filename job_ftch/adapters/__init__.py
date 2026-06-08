"""Runtime adapters for integrating job_ftch with external orchestrators."""

from job_ftch.adapters.dagster_adapter import create_definitions
from job_ftch.adapters.fastapi_adapter import create_app
from job_ftch.adapters.faststream_adapter import register_faststream_handlers
from job_ftch.adapters.mcp_adapter import create_mcp_server

__all__ = [
    "create_app",
    "create_definitions",
    "create_mcp_server",
    "register_faststream_handlers",
]
