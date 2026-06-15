# Tech Debt

## Multi-tenancy hidden in the Telegram bot (2026-06-15)
The bot was simplified to single-tenant UX. Tenant arguments were removed from ALL
bot commands (`/run`, `/reset`, `/reset_dedup`, `/digest`, `/search`, `/status`,
`/sources`, `/addsource`, `/addsources`, `/disablesource`, `/setposting`,
`/setnotify`). The `/tenants` command was removed entirely.

Handlers now call `runner.default_tenant_id()` (= first configured tenant) internally.
The underlying `TenantRunner` and library remain multi-tenant.

**To restore multi-tenant UX:** re-add optional tenant arg parsing in the handlers and
re-expose `/tenants`. The `run_all()` method on TenantRunner still exists but is no
longer wired to any bot command.

## ozon.tech source fails (2026-06-15)
`career_site:ozon_tech_vacancies` returns 403, escalates to curl_stealth (now works
after adding the `stealth` extra), then falls back to `api_sniffer` which needs a
Playwright Chromium browser not installed in the bot image:
`BrowserType.launch: Executable doesn't exist ... chrome-headless-shell`.
The source fails gracefully (logged warning, contributes 0 items).
**Fix options:** add `RUN playwright install --with-deps chromium` to
`adapters/telegram_bot/Dockerfile` (~400MB image growth), or `/disablesource` ozon.

## SemanticPrefilter is keyword-only (2026-06-15)
The prefilter runs BEFORE LLM extraction, so job embeddings are not yet available.
It scores items by keyword overlap against the user profile only (default catalog
mixing was removed). Embedding/negative-example scoring happens later in
`MultiProfileMatchNode` (final ranking / digest).
**Possible improvement:** a cheap embedding gate immediately after extraction to drop
near-duplicates of negative examples before they reach the catalog.
