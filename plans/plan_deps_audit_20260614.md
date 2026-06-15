# Plan: Full dependency audit for bot Docker image

## Problem

`registry.py::load_extensions()` eagerly imports ALL infrastructure modules at startup,
including career-site monitors. Some of those modules have hard top-level imports of
optional packages (e.g. `import jmespath` in `nextdata_utils.py`).

The Dockerfile currently installs: `.[bot,postgres,openai,qdrant,feeds]`
Missing: `site_scrapers` extra (provides `jmespath`), and potentially others.

## Task

### Step 1: Audit missing imports

Scan ALL Python files under `job_ftch/infrastructure/` that are imported by
`load_extensions()` in `registry.py`. Find every top-level `import X` or
`from X import` where X is a third-party package NOT in the core `dependencies`
list in `pyproject.toml` AND NOT in the extras currently installed by the Dockerfile.

Currently installed extras: `bot`, `postgres`, `openai`, `qdrant`, `feeds`

Core deps (always installed):
- pydantic, pydantic-settings, httpx, selectolax, rapidfuzz,
  opentelemetry-api, opentelemetry-sdk, structlog, defusedxml, slowapi

Bot extra adds: aiogram, telethon, fastapi, uvicorn, aiosqlite, pypdf,
  python-docx, pdfminer.six, PyYAML
Postgres extra adds: asyncpg
OpenAI extra adds: openai, instructor
Qdrant extra adds: qdrant-client
Feeds extra adds: feedparser

Find anything ELSE imported at module level that would cause ImportError at startup.

### Step 2: Fix the Dockerfile

Update `adapters/telegram_bot/Dockerfile` Step 5 (the pip install line) to add
any missing extras. Based on the error `jmespath` is needed → add `site_scrapers`.

Change:
```dockerfile
RUN pip install --no-cache-dir ".[bot,postgres,openai,qdrant,feeds]"
```

To (at minimum):
```dockerfile
RUN pip install --no-cache-dir ".[bot,postgres,openai,qdrant,feeds,site_scrapers]"
```

If the audit in Step 1 reveals other missing packages, add their extras too.
If a package has no extra (it's just missing from pyproject.toml entirely),
add it to the `bot` extra in `pyproject.toml`.

### Step 3: Rebuild and verify

After fixing the Dockerfile:
```powershell
docker compose up -d --build
```

Wait ~15 seconds, then:
```powershell
docker compose logs bot --tail=40
```

The bot MUST NOT crash. Success looks like structlog INFO lines about the bot
starting (e.g. "Starting polling", "Dispatcher started", aiogram startup messages).
If another ModuleNotFoundError appears, fix that too before reporting done.

Repeat rebuild+check until the bot starts cleanly.

### Step 4: Report

List all extras/packages that were added and why.
