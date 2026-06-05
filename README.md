# job_ftch

![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-early%20development-orange.svg)

**job_ftch** is an open-source async pipeline that ingests vacancies from Telegram channels, groups, post comments, and company career sites. It normalizes them to a unified Job schema and outputs structured JSON.

## Why?
AI role vacancies (LLM Engineer, AI PM, MLOps, AgentOps, AI Infra) are scattered across dozens of Telegram chats. No single structured source exists for these niche but rapidly growing roles.

## Quick Start
```bash
git clone https://github.com/[owner]/job_ftch
cd job_ftch
uv sync
cp .env.dev.example .env
# edit .env with your Telegram API credentials
uv run python app.py
```

## Architecture
Hexagonal (Ports & Adapters) — see [docs/architecture.md](docs/architecture.md) for details.

## Documentation
Documentation on architecture, vision, rules, and tech stack can be found in the [docs/](docs/) directory.

## Contributing
We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License
MIT
