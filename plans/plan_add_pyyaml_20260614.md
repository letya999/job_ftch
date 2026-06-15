# Plan: Add PyYAML to bot extra in pyproject.toml

## Problem
Bot container crashes on startup:
  ModuleNotFoundError: No module named 'yaml'
  RuntimeError: PyYAML is required to load tenant YAML config files.

The `job_ftch/application/tenant_loader.py` tries to `import yaml` to parse tenant YAML
configs from `adapters/telegram_bot/config/tenants/`. PyYAML is not listed in the `bot`
extra in `pyproject.toml`, so it's missing from the Docker image.

## Fix

### File: `pyproject.toml`

Find the `[project.optional-dependencies]` section and the `bot` extra. Add `"PyYAML>=6.0"` to it.

Current `bot` extra (approximate):
```toml
bot = [
    "aiogram>=3.7",
    "telethon>=1.36",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "aiosqlite>=0.20",
    "pypdf>=4.0",
    "python-docx>=1.1",
    "pdfminer.six>=20231228",
]
```

Updated `bot` extra:
```toml
bot = [
    "aiogram>=3.7",
    "telethon>=1.36",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "aiosqlite>=0.20",
    "pypdf>=4.0",
    "python-docx>=1.1",
    "pdfminer.six>=20231228",
    "PyYAML>=6.0",
]
```

## After the fix

Run:
```powershell
docker compose up -d --build
docker compose logs bot --tail=40
```

Verify the bot starts successfully (no ModuleNotFoundError, no RuntimeError about yaml).

Do NOT run the full pytest suite — this is a trivial dependency addition; tests already pass.
Just rebuild the container and check the logs.
