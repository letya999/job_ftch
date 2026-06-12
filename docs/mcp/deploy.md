# MCP Deployment

## Local stdio

```bash
job_ftch mcp-server --configs-dir ./config/tenants
```

## Local HTTP

```bash
job_ftch mcp-server --configs-dir ./config/tenants --transport http --host 127.0.0.1 --port 8000
```

## Docker

Use `Dockerfile.mcp` and mount a tenant-config volume at `/app/config`.

## systemd

Run the same CLI command under a dedicated service user and restart on failure.
