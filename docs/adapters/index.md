# adapters Index

`docs/adapters/`

Generated index for navigation. Edit source documents, then rerun `uv run python scripts/build_index_docs.py`.

## Files On This Level

- [Dagster adapter](dagster.md) - Scaffold adapter: exposes configured sources as Dagster definitions. (Updated: 2026-07-28)
- [FastAPI adapter](fastapi.md) - Scaffold ASGI adapter for running/searching jobs through HTTP endpoints. (Updated: 2026-07-28)
- [FastStream adapter](faststream.md) - Scaffold message-queue adapter around a configured PipelineBuilder. (Updated: 2026-07-28)
- [MCP adapter](mcp_adapter.md) - FastMCP tenant server exposing pipeline tools and job resources. (Updated: 2026-08-07)
- [MCP client setup](mcp_client_setup.md) - How to point local MCP clients at the job_ftch tenant server. (Updated: 2026-08-07)
- [MCP deployment](mcp_deploy.md) - Three profiles: local process (Win/macOS/Linux/WSL), VPS systemd, Docker. (Updated: 2026-08-07)
- [Runtime и env: где правда](runtime_and_env.md) - Короткая карта того, какие файлы являются source of truth для конфигурации и переменных окружения. (Updated: 2026-07-28)
- [Telegram Bot Deploy](telegram_bot_deploy.md) - - Use polling mode by leaving `JOB_FTCH_AUTH_TELEGRAM_BOT_WEBHOOK_URL` unset. (Updated: 2026-07-28)
