# MCP adapter

```python
from job_ftch import configure
from adapters.mcp.adapter import create_mcp_server

builder = configure("config/tenant.yaml")
server = create_mcp_server(builder)
server.run()
```
